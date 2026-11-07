"""Owner and authenticated-team validation journeys on isolated stores, desktop/mobile."""
import copy
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.workbench_browser import serve
from tools.workspace_fingerprint import fingerprints
from app.research.catalog import digest


def main():
    from playwright.sync_api import sync_playwright, expect
    checks, errors = [], []
    base = "http://127.0.0.1:8796"
    with tempfile.TemporaryDirectory(prefix="artha-validation-browser-", dir=ROOT/"data") as directory:
        process = multiprocessing.get_context("spawn").Process(target=serve, args=(str(Path(directory)/"paper.sqlite3"),))
        process.start()
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base+"/api/health", timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Isolated server did not start")
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page(viewport=dict(width=1440, height=1000))
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base+"/#contracts", wait_until="networkidle")
                page.locator("#ct-tab-validation").click()
                root = page.locator("#contract-validation-root")
                expect(root.locator("[data-validation-status]")).to_have_attribute("data-validation-status", "PASS_CONTROLLED", timeout=15000)
                expect(root).to_contain_text("External acceptance pending")
                root.locator("[data-fv-category]").select_option("CHARGES")
                expect(root.locator(".fv-case")).to_have_count(5)
                root.locator(".fv-case").first.locator("summary").click()
                expect(root.locator(".fv-case").first).to_contain_text("Expected")
                with page.expect_download() as download:
                    root.locator("[data-fv-download]").click()
                report = json.loads(Path(download.value.path()).read_text())
                assert report["core"]["all_controlled_checks_passed"]
                assert digest(report["core"]) == report["report_hash"], "Download changed numeric representation"
                checks.append("Owner inspects real current-build goldens, filters cases and downloads exact evidence")
                root.locator("[data-fv-category]").select_option("")
                root.locator("[data-fv-result]").select_option("fail")
                expect(root).to_contain_text("No cases match")
                page.set_viewport_size(dict(width=390, height=844))
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), "Mobile validation overflow"
                page.screenshot(path=str(ROOT/"docs/screenshots/mvp4-validation-mobile-controlled.png"), full_page=True)
                checks.append("Mobile validation is usable and the empty failure filter is clear")
                actual = page.request.get(base+"/api/contract-research/validation").json()
                stale = copy.deepcopy(actual)
                stale.update(status="STALE", current=False, controlled_checks_passed=False, message="Earlier source snapshot. Run validation again.")
                page.route("**/api/contract-research/validation", lambda route: route.fulfill(json=stale))
                root.locator("[data-fv-refresh]").click()
                expect(root.locator("[data-validation-status]")).to_have_attribute("data-validation-status", "STALE")
                checks.append("Earlier-build evidence stays visibly stale instead of claiming a current pass")
                page.unroute("**/api/contract-research/validation")
                page.locator("#ct-tab-definition").click()
                expect(page.locator("#contracts-root")).to_be_visible()
                expect(root).to_be_hidden()
                assert page.request.get(base+"/api/team/validation").status == 401
                team = browser.new_page(viewport=dict(width=1440, height=1000))
                team.on("pageerror", lambda error: errors.append(str(error)))
                team.goto(base+"/team", wait_until="networkidle")
                team.locator("#setup-name").fill("Controlled validation team")
                team.locator("#setup-key").fill((Path(directory)/"teams/default/bootstrap.key").read_text())
                team.locator("#auth-email").fill("validation@example.test")
                team.locator("#auth-password").fill("Controlled browser password 1234")
                team.locator("#auth-submit").click()
                team.locator('[data-tab="validation"]').click()
                expect(team.locator("[data-validation-status]")).to_have_attribute("data-validation-status", "PASS_CONTROLLED", timeout=15000)
                expect(team.locator("#panel")).to_contain_text("Genuine-data calibration")
                team.screenshot(path=str(ROOT/"docs/screenshots/mvp4-validation-desktop-controlled.png"), full_page=True)
                checks.append("Authenticated team members inspect the same software evidence; anonymous reads fail")
                assert not errors, errors
                browser.close()
        finally:
            process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join()
    output = dict(checks=checks, javascript_errors=errors, passed=True, identity=fingerprints(),
        evidence_kind="CONTROLLED_BROWSER", real_orders_sent=False)
    (ROOT/"docs/financial-validation-browser.json").write_text(json.dumps(output, indent=2)+"\n")
    print(json.dumps(dict(passed=True, checks=checks, javascript_errors=errors)))


if __name__ == "__main__":
    main()
