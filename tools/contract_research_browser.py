"""MVP-2 guided acceptance on isolated stores with actual process workers."""
import copy
import json
import multiprocessing
import subprocess
from pathlib import Path
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench_browser import serve

def main():
    from playwright.sync_api import sync_playwright,expect
    from tools.workspace_fingerprint import fingerprints
    checks,errors=[],[]
    base="http://127.0.0.1:8796"
    def passed(message):
        checks.append(message);print(json.dumps(dict(passed_stage=message)),flush=True)
    with tempfile.TemporaryDirectory(prefix="artha-contract-research-",dir=ROOT/"data") as directory:
        process=multiprocessing.get_context("spawn").Process(target=serve,args=(str(Path(directory)/"paper.sqlite3"),))
        process.start()
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base+"/api/health",timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError("Controlled research server did not start")
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
                page=browser.new_page(viewport=dict(width=1440,height=1000),accept_downloads=True)
                page.on("pageerror",lambda error:errors.append(str(error)))
                page.goto(base+"/#contracts",wait_until="networkidle")
                before=page.request.get(base+"/api/state").json()
                page.locator("#ct-tab-research").click()
                expect(page.locator("#cr-freeze")).to_be_visible()
                expect(page.locator("#cr-baseline")).to_be_disabled()
                page.locator("#cr-example").click()
                expect(page.locator("#cr-message")).to_contain_text("Fictional example loaded",timeout=15000)
                expect(page.locator("#cr-contract")).not_to_have_value("")
                passed("explicit fictional example creates an editable contract and immutable receipt-clock dataset")
                page.locator("#cr-freeze-button").click()
                expect(page.locator("#cr-message")).to_contain_text("Acknowledge")
                assert not page.request.get(base+"/api/contract-research/protocols").json()
                page.locator("#cr-ack").check()
                page.locator("#cr-freeze-button").click()
                expect(page.locator("#cr-message")).to_contain_text("Protocol frozen",timeout=15000)
                expect(page.locator("#cr-baseline")).to_be_enabled()
                expect(page.locator("#cr-binding")).to_contain_text("RECORDED RECEIPT")
                protocol=page.request.get(base+"/api/contract-research/protocols").json()[0]
                passed("guided protocol binds the saved revision, source, build, risk and observation cutoff with explicit acknowledgment")
                page.locator("#cr-baseline").click()
                expect(page.locator("#cr-trials")).to_contain_text("COMPLETED",timeout=30000)
                expect(page.locator("#cr-repeat")).to_be_enabled(timeout=15000)
                page.locator("#cr-repeat").click()
                expect(page.locator("#cr-trials tbody tr")).to_have_count(2,timeout=15000)
                expect(page.locator("#cr-trials tbody tr").nth(1)).to_contain_text("COMPLETED",timeout=30000)
                expect(page.locator("#cr-stress")).to_be_enabled(timeout=15000)
                page.locator("#cr-stress").click()
                expect(page.locator("#cr-trials tbody tr")).to_have_count(3,timeout=15000)
                expect(page.locator("#cr-trials tbody tr").nth(2)).to_contain_text("COMPLETED",timeout=30000)
                details=page.request.get(base+"/api/contract-research/protocols/"+protocol["id"]).json()
                assert all(t["summary"]["fills"]==4 for t in details["trials"]),details["trials"]
                assert details["trials"][0]["summary"]["economic_hash"]==details["trials"][1]["summary"]["economic_hash"]
                assert details["trials"][2]["summary"]["net_pnl"]<details["trials"][0]["summary"]["net_pnl"]
                passed("real isolated process workers complete baseline, identical reproduction and adverse cost/latency stress")
                expect(page.locator("#cr-capture")).to_be_enabled()
                page.locator("#cr-capture").click()
                expect(page.locator("#cr-findings .cr-finding")).to_have_count(5,timeout=15000)
                expect(page.locator("#cr-findings")).to_contain_text("reproducibility")
                expect(page.locator("#cr-findings")).to_contain_text("UNVERIFIED")
                with page.expect_download() as downloaded:
                    page.locator("[data-cr-export]").first.click()
                summary=json.loads(Path(downloaded.value.path()).read_text())
                assert summary["attachments"]=={}
                assert all("positions" not in t["report"] and "account" not in t["report"] for t in summary["core"]["trials"])
                page.locator("#cr-trace").check()
                with page.expect_download() as downloaded:
                    page.locator("[data-cr-export]").first.click()
                packet=json.loads(Path(downloaded.value.path()).read_text())
                assert len(packet["attachments"])==3
                verified=subprocess.run([sys.executable,"-B",str(ROOT/"tools/verify_contract_packet.py"),str(downloaded.value.path()),
                    "--expected-protocol",protocol["id"],"--expected-build",protocol["core"]["build"]["code_hash"],
                    "--expected-dataset",protocol["core"]["dataset_snapshot"]["id"],"--require-reproduction"],
                    capture_output=True,text=True,timeout=20)
                assert verified.returncode==0,verified.stdout+verified.stderr
                assert json.loads(verified.stdout)["repeat_results_match"]
                passed("five independent findings stay visible; quote-bearing traces export only after explicit permitted-use acknowledgment")
                page.locator("#cr-file").set_input_files(dict(name="evidence.json",mimeType="application/json",buffer=json.dumps(packet).encode()))
                expect(page.locator("#cr-verification")).to_contain_text("Baseline and repeat match.")
                broken=copy.deepcopy(packet);broken["core"]["trials"][0]["report"]["result"]["net_pnl"]+=1
                page.locator("#cr-file").set_input_files(dict(name="tampered.json",mimeType="application/json",buffer=json.dumps(broken).encode()))
                expect(page.locator("#cr-verification")).to_contain_text("Packet hash mismatch")
                passed("UI and offline verifier check pinned identities and economic traces; modified results fail integrity")
                expect(page.locator("#cr-baseline")).to_be_enabled()
                page.locator("#cr-baseline").click()
                expect(page.locator("#cr-trials tbody tr")).to_have_count(4,timeout=15000)
                expect(page.locator("#cr-trials tbody tr").nth(3)).to_contain_text("COMPLETED",timeout=30000)
                expect(page.locator("#cr-packets")).to_contain_text("Historical packet",timeout=15000)
                page.locator("#cr-open-definition").click()
                expect(page.locator("#ct-name")).to_have_value(protocol["core"]["definition"]["name"])
                page.locator("#ct-tab-research").click()
                page.set_viewport_size(dict(width=390,height=844))
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2")
                page.locator("#cr-findings").scroll_into_view_if_needed()
                page.screenshot(path=str(ROOT/"docs/screenshots/contract-research-controlled-mobile.png"),full_page=True)
                page.set_viewport_size(dict(width=1440,height=1000));page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(ROOT/"docs/screenshots/contract-research-controlled-desktop.png"))
                passed("additional attempts make earlier packets historical; definition handoff and mobile layout remain usable")
                page.get_by_role("button",name="Strategy Builder",exact=True).click()
                expect(page.locator("#strategy-templates [data-template]")).to_have_count(51)
                page.get_by_role("button",name="Trade",exact=True).click()
                expect(page.locator("#view-trade")).to_be_visible()
                after=page.request.get(base+"/api/state").json()
                assert all(before[key]==after[key] for key in ("account","positions","orders"))
                pilot=page.request.get(base+"/api/pilot/status").json()
                assert not pilot["armed"] and not pilot["orders"] and not pilot["transport_enabled"]
                passed("existing views remain available; research changes neither the main financial state nor broker authority")
                browser.close()
        finally:
            process.terminate();process.join(12)
            if process.is_alive():process.kill();process.join()
    assert not errors,errors
    result=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_data=True,real_provider_calls=False,real_orders_sent=False,identity=fingerprints()["sha256"])
    (ROOT/"docs/contract-research-browser-validation.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result),flush=True)

if __name__=="__main__":main()
