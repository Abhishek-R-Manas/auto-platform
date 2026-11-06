"""Controlled MVP-1 journeys on isolated databases; no broker or AI provider requests."""
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from workbench_browser import serve


def main():
    from playwright.sync_api import sync_playwright, expect
    from test_contracts import definition, import_quotes
    from tools.workspace_fingerprint import fingerprints
    checks, errors = [], []
    base = "http://127.0.0.1:8796"

    def passed(message):
        checks.append(message)
        print(json.dumps({"passed_stage": message}), flush=True)

    with tempfile.TemporaryDirectory(prefix="artha-contracts-browser-", dir=ROOT / "data") as directory:
        process = multiprocessing.get_context("spawn").Process(target=serve, args=(str(Path(directory) / "paper.sqlite3"),))
        process.start()
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + "/api/health", timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Controlled contract server did not start")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#contracts", wait_until="networkidle")
                expect(page.locator("#ct-form")).to_be_visible()
                expect(page.locator("#ct-file")).not_to_be_visible()
                assert page.locator("#view-contracts").bounding_box()["y"] < page.locator(".page-footer").bounding_box()["y"]
                expect(page.get_by_role("button", name="Strategy Contracts", exact=True)).to_be_visible()
                before = page.request.get(base + "/api/state").json()
                passed("separate sidebar option and direct hash route open the contract form")
                page.locator("#ct-check").click()
                expect(page.locator("#ct-result-state")).to_have_text("Definition complete")
                expect(page.locator("#ct-result")).to_contain_text("DATASET REQUIRED")
                expect(page.locator("#ct-result")).to_contain_text("Customer evidence")
                passed("illustrative definition computes bounded payoff while missing data stays visible")
                page.locator('[data-contract-leg="0"] [data-leg-field="premium_paise"]').fill("100.01")
                expect(page.locator("#ct-result-state")).to_have_text("Changed — check this definition")
                page.locator("#ct-check").click()
                expect(page.locator("#ct-result-state")).to_have_text("Definition needs changes")
                expect(page.locator("#ct-result")).to_contain_text("TICK")
                passed("editing invalidates the displayed assessment and invalid ticks are blocked")
                page.locator('[data-contract-leg="0"] [data-leg-field="premium_paise"]').fill("100")
                page.locator("#ct-name").fill("Controlled original <img src=x onerror=alert(1)>")
                page.locator("#ct-save").click()
                expect(page.locator("#ct-save-state")).to_contain_text("Saved revision")
                expect(page.locator("#ct-saved-list [data-contract-open]")).to_have_count(1)
                assert page.locator("#ct-saved-list img").count() == 0
                first = page.request.get(base + "/api/contracts").json()[0]["id"]
                passed("immutable definition saved and untrusted names rendered as text")
                page.locator("#ct-name").fill("Controlled low-budget revision")
                page.locator("#ct-loss").fill("1")
                page.locator("#ct-save").click()
                expect(page.locator("#ct-result-state")).to_have_text("Definition needs changes")
                expect(page.locator("#ct-saved-list [data-contract-open]")).to_have_count(2)
                rows = page.request.get(base + "/api/contracts").json()
                assert rows[0]["parent_id"] == first
                page.locator('[data-contract-open="' + first + '"]').click()
                expect(page.locator("#ct-loss")).to_have_value("5000")
                expect(page.locator("#ct-result-state")).to_have_text("Definition complete")
                passed("failed research revision retains its parent and original evidence")
                with page.expect_download() as download_info:
                    page.locator("#ct-export-saved").click()
                exported = json.loads(Path(download_info.value.path()).read_text())
                assert exported["id"] == first
                page.locator("#ct-new").click()
                page.locator("#ct-file").set_input_files({"name": "saved-contract.json", "mimeType": "application/json", "buffer": json.dumps(exported).encode()})
                expect(page.locator("#ct-name")).to_have_value(exported["core"]["definition"]["name"])
                expect(page.locator("#ct-save-state")).to_have_text("Unsaved definition")
                passed("saved export imports as an unsaved definition without trusting its old assessment")
                unsupported = definition()
                unsupported["rules"]["adjustments"] = "ROLL"
                page.locator("#ct-file").set_input_files({"name": "unsupported.json", "mimeType": "application/json", "buffer": json.dumps(unsupported).encode()})
                expect(page.locator("#toast")).to_contain_text("rules.adjustments")
                page.locator("#ct-file").set_input_files({"name": "invalid.json", "mimeType": "application/json", "buffer": b"{broken"})
                expect(page.locator("#toast")).to_have_class(__import__("re").compile("error"))
                assert len(page.request.get(base + "/api/contracts").json()) == 2
                passed("invalid JSON and unsupported adjustments do not create records")
                body = import_quotes(SimpleNamespace(import_data=lambda value: value), definition())
                response = page.request.post(base + "/api/research/datasets/import", data=body.model_dump(mode="json"), headers={"X-Artha-Client": "paper-workspace"})
                assert response.ok, response.text()
                dataset_id = response.json()["id"]
                page.reload(wait_until="networkidle")
                page.locator("#ct-dataset").select_option(dataset_id)
                page.locator("#ct-check").click()
                expect(page.locator("#ct-result")).to_contain_text("SYNTHETIC DATA")
                expect(page.locator("#ct-result")).to_contain_text("RECEIPT TIMES")
                passed("actual dataset compatibility is inspected without promoting synthetic or receipt-time evidence")
                page.locator("#ct-example").select_option("IRON_CONDOR")
                page.locator("#ct-check").click()
                expect(page.locator("#ct-result-state")).to_have_text("Definition complete")
                expect(page.locator("[data-contract-leg]")).to_have_count(4)
                page.evaluate("const b=document.createElement('div');b.textContent='CONTROLLED CONTRACT FIXTURE · FICTIONAL INPUTS';b.style='position:fixed;bottom:0;right:0;background:#253858;color:white;padding:5px 10px;font-size:10px;z-index:9999';document.body.append(b)")
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(ROOT / "docs/screenshots/contracts-controlled-desktop.png"), full_page=False)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+2")
                expect(page.locator("#ct-save")).to_be_visible()
                page.screenshot(path=str(ROOT / "docs/screenshots/contracts-controlled-mobile.png"), full_page=True)
                passed("iron-condor example and mobile layout retain editable legs without page overflow")
                page.set_viewport_size({"width": 1440, "height": 1000})
                for name, view in [("Strategy Builder", "builder"), ("My Strategies", "saved"), ("Trade", "trade")]:
                    page.get_by_role("button", name=name, exact=True).click()
                    expect(page.locator("#view-" + view)).to_be_visible()
                assert page.locator("#strategy-templates [data-template]").count() == 51
                page.get_by_role("button", name="Strategy Contracts", exact=True).click()
                expect(page.locator("#ct-form")).to_be_visible()
                after = page.request.get(base + "/api/state").json()
                assert before["account"] == after["account"] and before["positions"] == after["positions"] and before["orders"] == after["orders"]
                pilot = page.request.get(base + "/api/pilot/status").json()
                assert not pilot["armed"] and not pilot["orders"]
                passed("existing builder, saved strategies and trading views remain available; account and broker authority are unchanged")
                browser.close()
        finally:
            process.terminate()
            process.join(12)
            if process.is_alive():
                process.kill()
                process.join()
    assert not errors, errors
    result = dict(passed=len(checks), checks=checks, javascript_errors=errors, controlled_data=True, real_provider_calls=False, real_orders_sent=False, identity=fingerprints()["sha256"])
    (ROOT / "docs/contracts-browser-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
