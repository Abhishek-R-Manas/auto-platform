"""Two independent authenticated browser contexts, real workers and isolated team stores."""
import json,multiprocessing,os,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
PASSWORD="Controlled team browser password 12345"

def serve(root):
    import uvicorn
    from app.team.api import create_team_app
    for key in ("KITE_API_KEY","KITE_API_SECRET","KITE_ACCESS_TOKEN","ARTHA_AI_API_KEY","OPENAI_API_KEY","ARTHA_LIVE_TRANSPORT_ENABLED"):os.environ.pop(key,None)
    uvicorn.run(create_team_app(root),host="127.0.0.1",port=8797,access_log=False,log_level="error",timeout_graceful_shutdown=3)

def main():
    from playwright.sync_api import sync_playwright,expect
    from tools.workspace_fingerprint import fingerprints
    checks,errors=[],[];base="http://127.0.0.1:8797"
    def passed(message):
        checks.append(message);print(json.dumps(dict(passed_stage=message)),flush=True)
    with tempfile.TemporaryDirectory(prefix="artha-team-browser-",dir=ROOT/"data") as tmp:
        root=Path(tmp)/"organization"
        process=multiprocessing.get_context("spawn").Process(target=serve,args=(str(root),));process.start()
        try:
            for _ in range(100):
                try:urllib.request.urlopen(base+"/api/health",timeout=1).close();break
                except OSError:time.sleep(.1)
            else:raise RuntimeError("Controlled team server did not start")
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
                author_context=browser.new_context(viewport=dict(width=1440,height=1100),accept_downloads=True)
                reviewer_context=browser.new_context(viewport=dict(width=1440,height=1100))
                author=author_context.new_page();reviewer=reviewer_context.new_page()
                for page in (author,reviewer):page.on("pageerror",lambda error:errors.append(str(error)))
                author.goto(base+"/team",wait_until="networkidle")
                expect(author.locator("#auth-submit")).to_have_text("Initialize organization")
                assert author.request.get(base+"/api/team/dashboard").status==401
                for route in ("/api/state","/api/orders","/api/pilot/status","/api/research/runs"):
                    assert author.request.get(base+route).status==404
                author.locator("#setup-name").fill("Controlled research team")
                author.locator("#setup-key").fill((root/"bootstrap.key").read_text())
                author.locator("#auth-email").fill("author@example.test");author.locator("#auth-password").fill(PASSWORD)
                author.locator("#auth-submit").click()
                expect(author.locator("#freeze-form")).to_be_visible(timeout=15000)
                assert not (root/"bootstrap.key").exists()
                passed("one-time owner setup creates an authenticated organization; standalone service exposes no owner or broker APIs")
                author.locator('[data-tab="members"]').click()
                author.locator("#invite-name").fill("Reviewer <img src=x onerror=alert(1)>")
                author.locator("#invite-email").fill("reviewer@example.test")
                author.locator('[name="invite-role"][value="RESEARCHER"]').uncheck();author.locator('[name="invite-role"][value="REVIEWER"]').check()
                author.locator('#invite-form button').click()
                expect(author.locator("#invitation-link")).to_be_visible()
                invite=author.locator("#invitation-link").input_value()
                assert author.locator("#members-list img").count()==0
                reviewer.goto(invite,wait_until="networkidle")
                expect(reviewer.locator("#auth-submit")).to_have_text("Accept invitation")
                assert "#invite" not in reviewer.url
                reviewer.locator("#auth-password").fill(PASSWORD);reviewer.locator("#auth-submit").click()
                expect(reviewer.locator("#auth-submit")).to_have_text("Sign in",timeout=15000)
                reviewer.locator("#auth-email").fill("reviewer@example.test");reviewer.locator("#auth-password").fill(PASSWORD);reviewer.locator("#auth-submit").click()
                expect(reviewer.locator("#freeze-form")).to_be_visible(timeout=15000)
                expect(reviewer.locator("#load-example")).to_be_disabled()
                session=reviewer.request.get(base+"/api/team/session").json()
                forbidden=reviewer.request.post(base+"/api/team/example",data=dict(request_id="forbidden-example"),headers={"X-Artha-Client":"paper-workspace","X-Artha-CSRF":session["csrf"]})
                assert forbidden.status==403
                passed("single-use invitation lets the reviewer choose a private password; role restrictions are enforced by the server and names render as text")
                author.locator('[data-tab="research"]').click();author.locator("#load-example").click()
                expect(author.locator("#strategy-picker")).not_to_have_value("",timeout=15000)
                author.locator("#research-ack").check();author.locator("#freeze-button").click()
                expect(author.locator('[data-run="BASELINE"]')).to_be_enabled(timeout=15000)
                author.locator('[data-run="BASELINE"]').click()
                expect(author.locator("#research-detail tbody tr").nth(0)).to_contain_text("COMPLETED",timeout=30000)
                expect(author.locator('[data-run="REPRODUCE"]')).to_be_enabled()
                author.locator('[data-run="REPRODUCE"]').click()
                expect(author.locator("#research-detail tbody tr").nth(1)).to_contain_text("COMPLETED",timeout=30000)
                protocol_id=author.locator("#protocol-picker").input_value()
                author.locator("#capture").click()
                expect(author.locator("#packet-findings .entry")).to_have_count(5,timeout=15000)
                author.locator("#review-comment").fill("Attempting self-review must fail, including for the administrator.")
                author.locator("#submit-review").click()
                expect(author.locator("#message")).to_contain_text("independent reviewer",timeout=15000)
                passed("authenticated researcher completes baseline/reproduction and captures five findings; administrator self-review is rejected")
                reviewer.locator("#team-refresh").click();reviewer.locator('[data-tab="reviews"]').click()
                reviewer.locator("#protocol-picker").select_option(protocol_id)
                expect(reviewer.locator("#packet-findings .entry")).to_have_count(5,timeout=15000)
                reviewer.locator("#comment").fill("Controlled review <script>window.BAD=true</script>")
                reviewer.locator("#comment-form button").click()
                expect(reviewer.locator("#review-log")).to_contain_text("Controlled review")
                assert reviewer.locator("#review-log script").count()==0
                reviewer.locator("#review-comment").fill("Accounting/repeatability inspected. Synthetic source and statistical limits remain unqualified.")
                reviewer.locator("#submit-review").click()
                expect(reviewer.locator("#review-log")).to_contain_text("ACCEPT",timeout=15000)
                reviewer.locator("#scope-ack").check();reviewer.locator("#grant-scope").click()
                expect(reviewer.locator("#scope-list [data-revoke]")).to_have_count(1,timeout=15000)
                grant_id=reviewer.locator("[data-revoke]").first.get_attribute("data-revoke")
                passed("independent reviewer records attributable comments and snapshot acceptance, then grants a separate expiring paper-research scope")
                author.locator("#team-refresh").click();author.locator('[data-tab="research"]').click()
                expect(author.locator("#scope-picker option")).to_have_count(2,timeout=15000)
                author.locator("#scope-picker").select_option(grant_id);author.locator('[data-run="BASELINE"]').click()
                expect(author.locator("#research-detail tbody tr").nth(2)).to_contain_text("COMPLETED",timeout=30000)
                reviewer.locator("#revoke-comment").fill("Further execution must stop pending a new review.")
                reviewer.locator("[data-revoke]").first.click()
                expect(reviewer.locator("#scope-list")).to_contain_text("REVOKED",timeout=15000)
                author.locator("#scope-picker").select_option(grant_id);author.locator('[data-run="BASELINE"]').click()
                expect(author.locator("#message")).to_contain_text("revoked",timeout=15000)
                expect(author.locator("#research-detail tbody tr")).to_have_count(3)
                passed("approved operation uses the declared scope; revocation blocks further execution without consuming an attempt")
                author.locator("#open-reviews").click()
                expect(author.locator("#packet-findings .entry")).to_have_count(5,timeout=15000)
                author.locator("#trace-ack").check()
                with author.expect_download() as downloaded:author.locator("#export-packet").click()
                packet=json.loads(Path(downloaded.value.path()).read_text())
                assert packet["core"]["authenticated_context"]["organization_id"]
                author.locator("#verify-file").set_input_files(dict(name="team-evidence.json",mimeType="application/json",buffer=json.dumps(packet).encode()))
                expect(author.locator("#verification")).to_contain_text("Baseline and repeat match.",timeout=15000)
                passed("private evidence exports preserve authenticated authorship and verify without treating a review as live authority")
                author.locator('[data-tab="members"]').click();author.locator("#backup-create").click()
                expect(author.locator("#backup-result")).to_contain_text("Verified snapshot",timeout=30000)
                assert list((root/"backups").glob("team-*/manifest.json"))
                member=author.locator('[data-member]').filter(has=author.get_by_text("reviewer@example.test",exact=True))
                member.locator("[data-member-active]").uncheck();member.locator("[data-member-save]").click()
                expect(reviewer.locator("#auth-submit")).to_have_text("Sign in",timeout=15000)
                passed("administrator creates a verified private backup; disabling membership invalidates an existing reviewer session")
                author.locator('[data-tab="reviews"]').click()
                author.set_viewport_size(dict(width=390,height=844))
                assert author.evaluate("document.documentElement.scrollWidth<=innerWidth+2")
                author.screenshot(path=str(ROOT/"docs/screenshots/team-controlled-mobile.png"),full_page=True)
                author.set_viewport_size(dict(width=1440,height=1100));author.evaluate("window.scrollTo(0,0)")
                author.screenshot(path=str(ROOT/"docs/screenshots/team-controlled-desktop.png"))
                author.locator("#logout").click()
                expect(author.locator("#auth-submit")).to_have_text("Sign in",timeout=15000)
                assert author.request.get(base+"/api/team/dashboard").status==401
                passed("desktop/mobile review journeys fit the viewport and sign-out removes authenticated access")
                browser.close()
        finally:
            process.terminate();process.join(15)
            if process.is_alive():process.kill();process.join()
    assert not errors,errors
    result=dict(passed=len(checks),checks=checks,javascript_errors=errors,controlled_identities=True,controlled_data=True,
        actual_process_workers=True,real_provider_calls=False,real_orders_sent=False,offhost_destination_tested=False,identity=fingerprints()["sha256"])
    (ROOT/"docs/team-browser-validation.json").write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result),flush=True)

if __name__=="__main__":main()
