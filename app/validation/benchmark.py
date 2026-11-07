"""Golden constants exercised against the installed engine using fictional inputs.

Expectations are derived in the case rationales, never learned from engine outputs.
This establishes neither genuine-data calibration nor independent human acceptance.
"""
import copy
import csv
import io
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from app.charges import DEFAULTS, estimate
from app.contracts import ContractBook, assess, example, expiry_payoff
from app.contract_research import ContractResearch, verify_packet
from app.research.catalog import aware, digest
from app.research.jobs import Research, execute
from app.research.schemas import FeeRules, ImportData, Run, Terms

VERSION = "mvp4/1.0"
POLICY = dict(trade_pct=3, open_pct=5, daily_pct=2, weekly_pct=5, drawdown_pct=10,
              margin_pct=90, slippage_bps=0, overnight_stress_pct=50,
              gross_exposure_pct=1000, underlying_exposure_pct=500)


def money(value):
    return int((Decimal(str(value))*100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fixture(structure="CALL_VERTICAL", credit=False, lots=1, scenario="clean", fees=False):
    definition = example(structure)
    definition["strategy"]["lots"] = lots
    definition["risk"]["max_expiry_loss_paise"] = 500000*lots
    if credit:
        for leg in definition["strategy"]["legs"]:
            leg["side"] = "SELL" if leg["side"] == "BUY" else "BUY"
    instruments = [dict(id="NIFTY", symbol="NIFTY 50", kind="INDEX", underlying="NIFTY")]
    instruments.extend(dict(id=l["instrument_id"], symbol=l["instrument_id"], kind="OPT", underlying="NIFTY",
        exchange="NFO", lot_size=65, tick_size=.05, strike=l["strike_paise"]/100,
        option_type=l["kind"], expiry=l["expiry"]) for l in definition["strategy"]["legs"])
    rows = []
    times = ["09:29:59", "09:30:00", "09:30:01", "09:30:02", "09:30:10", "09:30:11",
             "14:59:59", "15:00:00", "15:00:01", "15:00:02", "15:00:10"]
    short_id = next(l["instrument_id"] for l in definition["strategy"]["legs"] if l["side"] == "SELL")
    premiums = {l["instrument_id"]: l["premium_paise"]/100 for l in definition["strategy"]["legs"]}
    for time in times:
        late = time >= "14:59:59"
        at = "2026-11-06T"+time+"+05:30"
        for instrument in instruments:
            identifier = instrument["id"]
            if scenario == "missing_exit" and late and identifier == short_id:
                continue
            if scenario == "incomplete_entry" and "09:30:01" <= time < "09:30:10" and identifier == short_id:
                continue
            price = premiums.get(identifier, 25000)
            if late and identifier in premiums:
                price += (2 if price == 20 else -5) if structure == "IRON_CONDOR" else (10 if price == 100 else 5)
            row = dict(instrument_id=identifier, timestamp=at, received_at=at, available_at=at, close=price,
                       bid=price-.05, ask=price+.05, bid_quantity=650, ask_quantity=650, volume=1000, iv=.2)
            if scenario == "wide_spread" and identifier in premiums and not late:
                row.update(bid=price-20, ask=price+20)
            if scenario == "no_depth" and identifier in premiums and not late:
                row.update(bid_quantity=0, ask_quantity=0)
            if scenario == "partial" and time == "15:00:00" and identifier in premiums:
                row.update(bid_quantity=65, ask_quantity=65)
            if scenario == "delayed" and time == "09:30:01" and identifier in premiums:
                row.update(close=1, bid=.95, ask=1.05, received_at="2026-11-06T15:00:20+05:30", available_at="2026-11-06T15:00:20+05:30")
            if scenario == "stale" and not late:
                row["timestamp"] = "2026-11-06T09:15:"+time[-2:]+"+05:30"
            if scenario == "receipt_reversed" and time == "09:30:00":
                row["received_at"] = "2026-11-06T09:29:58+05:30"
            if scenario == "crossed" and time == "09:30:00":
                row.update(bid=price+1, ask=price-1)
            rows.append(row)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    profile = {key: 0 for key in DEFAULTS}
    profile.update(source="MVP4_FICTIONAL_FEES", brokerage=1 if fees else 0)
    body = ImportData.model_validate(dict(manifest=dict(name="MVP-4 controlled benchmark", source="MVP4_FICTIONAL",
        source_kind="SYNTHETIC", frequency="QUOTE", observation_clock="EXCHANGE_AND_RECEIPT",
        instruments=instruments, fee_rules=[profile], notes="Fictional dated contracts; no market or licensing assertion"), content=stream.getvalue()))
    if scenario == "dated_fees":
        body.manifest.fee_rules.append(FeeRules(**dict(profile, brokerage=2, effective_from="2026-11-06T14:00:00+05:30")))
    return definition, body


@contextmanager
def sandbox():
    with tempfile.TemporaryDirectory(prefix="artha-mvp4-") as directory:
        research = Research(Path(directory)/"research")
        research.schedule = lambda identifier: None
        db = sqlite3.connect(Path(directory)/"workspace.sqlite3", check_same_thread=False)
        workspace = SimpleNamespace(db=db, lock=threading.RLock(), research=research)
        service = ContractResearch(workspace, ContractBook(workspace))
        try:
            yield service
        finally:
            research.close()
            db.close()


def freeze(service, definition, body, **changes):
    dataset = service.research.catalog.import_data(body)
    saved = service.book.save(dict(request_id="benchmark-definition", definition=definition, dataset_id=dataset["id"]))
    request = dict(request_id="benchmark-protocol", contract_id=saved["id"], dataset_id=dataset["id"],
        capital_paise=100000000, risk_policy=POLICY, observation_cutoff="2026-11-06T15:00:10+05:30",
        allow_unverified_data=True, max_trials=6)
    request.update(changes)
    return service.freeze(request)


def run_trial(service, protocol, kind="BASELINE"):
    trial = service.start(dict(request_id="benchmark-"+kind, protocol_id=protocol["id"], kind=kind))
    execute(service.research.root, trial["run_id"])
    actual = service.trial(trial["id"])
    if actual["status"] != "COMPLETED":
        raise ValueError("Controlled replay did not complete")
    return actual, service.research.report(trial["run_id"])


def replay_case(structure="CALL_VERTICAL", credit=False, lots=1, scenario="clean", fees=False, latency=0):
    with sandbox() as service:
        definition, body = fixture(structure, credit, lots, scenario, fees)
        protocol = freeze(service, definition, body, latency_seconds=latency)
        if scenario == "rejected_leg":
            config = copy.deepcopy(protocol["core"]["run_config"])
            config["execution"]["forced_reject_legs"] = ["C25100"]
            trial = service.research.create(config)
            execute(service.research.root, trial["id"])
            report = service.research.report(trial["id"])
            trial = dict(run_id=trial["id"])
        else:
            trial, report = run_trial(service, protocol)
        fills = service.research.events(trial["run_id"], kind="FILL", limit=1000)
        delta = sum(-f["quantity"]*money(f["price"])-money(f["fees"]) for f in fills)
        cash_delta = money(report["account"]["cash"])-100000000
        entry = aware(definition["rules"]["entry_at"])
        exit_at = aware(definition["rules"]["exit_at"])
        latency_respected = all(aware(f["time"]) >= (entry if aware(f["time"]) < exit_at else exit_at)+timedelta(seconds=latency) for f in fills)
        positions, protected = {}, True
        kinds = {l["instrument_id"]: l["kind"] for l in definition["strategy"]["legs"]}
        for fill in fills:
            key = fill["instrument_id"]
            positions[key] = positions.get(key, 0)+fill["quantity"]
            for kind in ("CE", "PE"):
                longs = sum(max(0, value) for i, value in positions.items() if kinds[i] == kind)
                shorts = sum(max(0, -value) for i, value in positions.items() if kinds[i] == kind)
                protected = protected and longs >= shorts
        return dict(net_pnl_paise=money(report["account"]["equity"])-100000000,
            ledger_delta_paise=delta, cash_delta_paise=cash_delta, accounting_match=delta == cash_delta,
            fees_paise=sum(money(f["fees"]) for f in fills), fill_count=len(fills),
            positions=len(report["positions"]), pending_orders=len(report["pending_orders"]),
            hedges_preserved=protected, fill_quantities=[f["quantity"] for f in fills],
            latency_respected=latency_respected,
            dataset_id=protocol["core"]["dataset_snapshot"]["id"], economic_hash=report["economic_hash"])


def payoff_case(structure, credit=False, lots=1, costs=4000):
    definition, _ = fixture(structure, credit, lots)
    result = expiry_payoff(definition["strategy"]["legs"], 65*lots, costs)
    return {key: result[key] for key in ("max_loss_paise", "max_profit_paise")}


def charges_case(side):
    profile = dict(brokerage=20, options_stt_sell=.002, exchange_opt=.001,
                   sebi_rate=.00001, stamp_opt=.0001, gst_rate=.18)
    return {key: money(value) for key, value in estimate("OPT", side, 10000, profile=profile).items()}


def definition_fault(code):
    definition, _ = fixture()
    if code == "EXPIRY":
        definition["rules"]["exit_at"] = definition["strategy"]["legs"][0]["expiry"]
    elif code == "TERMS_CUTOFF":
        definition["strategy"]["terms_effective_at"] = "2026-11-07T00:00:00+05:30"
    elif code == "LOSS_BUDGET":
        definition["risk"]["max_expiry_loss_paise"] = 1
    result = assess(definition)
    return dict(detected=any(f["code"] == code and f["status"] == "BLOCKED" for f in result["findings"]))


def ingestion_fault(scenario):
    with sandbox() as service:
        _, body = fixture(scenario=scenario)
        result = service.research.catalog.import_data(body)
        return dict(detected=result["status"] == "QUARANTINED")


def dated_terms(future=False):
    with sandbox() as service:
        definition, body = fixture()
        for instrument in body.manifest.instruments:
            if instrument.kind == "OPT":
                instrument.terms = [Terms(effective_at="2026-11-07T00:00:00+05:30" if future else "2026-11-05T00:00:00+05:30", lot_size=75)]
        dataset = service.research.catalog.import_data(body)
        result = service.book.check(definition, dataset["id"])
        blocked = any(f["code"] == "DATA_CONTRACT_TERMS" and f["status"] == "BLOCKED" for f in result["findings"])
        return dict(detected=blocked)


def reproducibility(tamper=False):
    with sandbox() as service:
        definition, body = fixture()
        p = freeze(service, definition, body)
        run_trial(service, p)
        run_trial(service, p, "REPRODUCE")
        record = service.capture(dict(request_id="benchmark-capture", protocol_id=p["id"]))
        packet = service.export(record["id"], True, True)
        if tamper:
            packet["core"]["trials"][0]["report"]["result"]["net_pnl"] += 1
        result = verify_packet(packet)
        return dict(detected=not result["integrity_valid"]) if tamper else dict(
            integrity_valid=result["integrity_valid"], trace_valid=result["trace_valid"], repeat_results_match=result["repeat_results_match"])


def exposed_evaluation():
    definition, body = fixture()
    with sandbox() as service:
        p = freeze(service, definition, body)
        config = copy.deepcopy(p["core"]["run_config"])
        config.update(training_end="2026-11-06T10:00:00+05:30", windows=[dict(label="HOLDOUT",
            start="2026-11-06T09:30:00+05:30", end="2026-11-06T15:00:00+05:30")])
        try:
            Run.model_validate(config)
        except ValueError as error:
            return dict(detected="Training cutoff overlaps evaluation" in str(error))
        return dict(detected=False)


def cases():
    result = []
    def add(identifier, category, label, expected, execute_case, rationale):
        result.append(dict(id=identifier, category=category, label=label, expected=expected,
                           execute=execute_case, rationale=rationale))
    for structure in ("CALL_VERTICAL", "PUT_VERTICAL", "IRON_CONDOR"):
        condor = structure == "IRON_CONDOR"
        add("payoff-"+structure.lower(), "PAYOFF", "CLEAN", dict(max_loss_paise=394000 if condor else 264000,
            max_profit_paise=256000 if condor else 386000), lambda s=structure: payoff_case(s),
            "65 units; 100-point wings; debit/credit 40 points; subtract 4000 paise costs at each terminal payoff.")
    add("payoff-call-credit", "PAYOFF", "CLEAN", dict(max_loss_paise=394000, max_profit_paise=256000),
        lambda: payoff_case("CALL_VERTICAL", True), "Credit 40*65*100; loss (100-40)*65*100; costs 4000 paise.")
    add("payoff-two-lots", "PAYOFF", "CLEAN", dict(max_loss_paise=524000, max_profit_paise=776000),
        lambda: payoff_case("CALL_VERTICAL", lots=2), "130 units; debit 520000 paise; width 1300000; costs 4000.")
    for side, values in (("BUY", dict(brokerage=2000, stt=0, exchange=1000, sebi=10, stamp=100, gst=542, dp=0, total=3652)),
                         ("SELL", dict(brokerage=2000, stt=2000, exchange=1000, sebi=10, stamp=0, gst=542, dp=0, total=5552))):
        add("fees-"+side.lower(), "CHARGES", "CLEAN", values, lambda s=side: charges_case(s),
            "Hypothetical INR 10000 turnover: brokerage 20, exchange 10, SEBI .10, GST (20+10+.10)*.18=5.418 -> 5.42; buy stamp 1; sell STT 20.")
    for structure in ("CALL_VERTICAL", "PUT_VERTICAL", "IRON_CONDOR"):
        add("replay-"+structure.lower(), "EXECUTION", "CLEAN", dict(net_pnl_paise=88400 if structure == "IRON_CONDOR" else 31200,
            accounting_match=True, positions=0, pending_orders=0, hedges_preserved=True, fill_count=8 if structure == "IRON_CONDOR" else 4),
            lambda s=structure: replay_case(s), "Vertical: (109.95-100.05+59.95-65.05)*65=312 INR. Condor: (2*1.90+2*4.90)*65=884 INR.")
    add("replay-credit", "EXECUTION", "CLEAN", dict(net_pnl_paise=-33800, positions=0, accounting_match=True, hedges_preserved=True),
        lambda: replay_case(credit=True), "Short 99.95->110.05, hedge 60.05->64.95: (-10.10+4.90)*65=-338 INR.")
    add("replay-fees", "CHARGES", "CLEAN", dict(net_pnl_paise=30800, fees_paise=400, accounting_match=True),
        lambda: replay_case(fees=True), "312 INR spread P&L minus four independently declared one-rupee order fees.")
    add("replay-partial", "EXECUTION", "CLEAN", dict(net_pnl_paise=62400, positions=0, accounting_match=True, hedges_preserved=True, fill_quantities=[130,-130,65,65,-130]),
        lambda: replay_case(lots=2, scenario="partial"), "Two lots: 2*312 INR; short buyback initially fills one lot; the hedge remains until the remaining short closes.")
    add("partial-fees", "CHARGES", "CLEAN", dict(net_pnl_paise=62000, fees_paise=400, fill_count=5, accounting_match=True, hedges_preserved=True),
        lambda: replay_case(lots=2, scenario="partial", fees=True), "Five fills belong to four orders: brokerage accumulates by order, so 624-4=620 INR; do not charge a second flat fee on the partial buyback.")
    add("rejected-leg", "EXECUTION", "FAULT", dict(net_pnl_paise=-650, fill_count=2, positions=0, hedges_preserved=True, accounting_match=True),
        lambda: replay_case(scenario="rejected_leg"), "Shared-engine rejection sensitivity: reject the short leg; unwind the filled long at a 0.10*65=6.50 INR spread loss. This is an isolated execution-layer scenario.")
    add("dated-fees", "CHARGES", "CLEAN", dict(net_pnl_paise=30600, fees_paise=600, accounting_match=True),
        lambda: replay_case(scenario="dated_fees", fees=True), "Entry orders cost 1 INR each; the two exits use the later 2 INR tariff. 312-2-4=306 INR.")
    add("replay-latency", "EXECUTION", "CLEAN", dict(net_pnl_paise=31200, positions=0, accounting_match=True, hedges_preserved=True, latency_respected=True),
        lambda: replay_case(latency=2), "Two-second acknowledgment latency must preserve clean-tape economics and hedge ordering.")
    for scenario in ("wide_spread", "no_depth", "stale"):
        add("reject-"+scenario, "EXECUTION", "FAULT", dict(fill_count=0, accounting_match=True, net_pnl_paise=0),
            lambda s=scenario: replay_case(scenario=s), "Bad entry evidence must produce no entry fills, rather than fabricated execution.")
    add("delayed-after-cutoff", "INFORMATION_CUTOFF", "CLEAN", dict(net_pnl_paise=31200, accounting_match=True, hedges_preserved=True),
        lambda: replay_case(scenario="delayed"), "One-rupee observations received after the evaluation cutoff cannot change execution.")
    add("unavailable-exit", "EXECUTION", "FAULT", dict(positions=2, hedges_preserved=True, accounting_match=True),
        lambda: replay_case(scenario="missing_exit"), "Unavailable short buyback retains both short and protective long; no fictional flat-book exit.")
    add("incomplete-entry", "EXECUTION", "FAULT", dict(positions=0, hedges_preserved=True, accounting_match=True),
        lambda: replay_case(scenario="incomplete_entry"), "Missing short observations expire the entry remainder and unwind filled protection.")
    for code in ("EXPIRY", "TERMS_CUTOFF", "LOSS_BUDGET"):
        add("definition-"+code.lower(), "CONTRACT", "FAULT", dict(detected=True), lambda c=code: definition_fault(c),
            "The named contract finding must block incompatible planned semantics.")
    for scenario in ("receipt_reversed", "crossed"):
        add("ingestion-"+scenario, "INFORMATION_CUTOFF", "FAULT", dict(detected=True), lambda s=scenario: ingestion_fault(s),
            "Impossible receipt chronology or crossed bid/ask must be quarantined.")
    add("dated-lot-change", "CONTRACT", "FAULT", dict(detected=True), dated_terms, "Effective 75-unit terms reject a 65-unit definition.")
    add("future-terms-excluded", "CONTRACT", "CLEAN", dict(detected=False), lambda: dated_terms(True),
        "A future 75-unit term must not overwrite the applicable 65-unit terms.")
    add("reproducibility", "REPRODUCIBILITY", "CLEAN", dict(integrity_valid=True, trace_valid=True, repeat_results_match=True),
        reproducibility, "Distinct pinned baseline and reproduction attempts must have equal economic traces.")
    add("tampered-result", "REPRODUCIBILITY", "FAULT", dict(detected=True), lambda: reproducibility(True),
        "A changed P&L must fail packet integrity.")
    add("exposed-holdout", "INFORMATION_CUTOFF", "FAULT", dict(detected=True), exposed_evaluation,
        "Training cannot include observations from the declared holdout window.")
    return result


def evaluate_case(case):
    expected = case["expected"]
    try:
        actual = case["execute"]()
        mismatches = {key: dict(expected=value, actual=actual.get(key)) for key, value in expected.items() if actual.get(key) != value}
        return dict(id=case["id"], category=case["category"], label=case["label"], expected=expected,
            actual=actual, passed=not mismatches, mismatches=mismatches, rationale=case["rationale"],
            expected_hash=digest(expected), evidence_kind="CONTROLLED_SYNTHETIC")
    except Exception as error:
        return dict(id=case["id"], category=case["category"], label=case["label"], expected=expected,
            actual={}, passed=False, error_type=type(error).__name__, mismatches={"execution": "Case did not complete"},
            rationale=case["rationale"], expected_hash=digest(expected), evidence_kind="CONTROLLED_SYNTHETIC")


def summarize(results):
    clean = [r for r in results if r["label"] == "CLEAN"]
    faulty = [r for r in results if r["label"] == "FAULT"]
    clean_failures = sum(not r["passed"] for r in clean)
    missed = sum(not r["passed"] for r in faulty)
    return dict(total=len(results), passed=sum(r["passed"] for r in results), clean_controls=len(clean),
        faulty_controls=len(faulty), clean_control_failures=clean_failures, missed_fault_controls=missed,
        clean_control_failure_rate=clean_failures/len(clean) if clean else None,
        missed_fault_control_rate=missed/len(faulty) if faulty else None,
        population_claim="Finite controlled cases only; not market-wide detection or profitability")
