import io, json, collections
from playwright.sync_api import sync_playwright
SCR="/tmp/claude-0/-home-user-command-ai/80d92365-c2e7-5519-96f9-57252960b5f8/scratchpad"
AUDIT=io.open(f"{SCR}/audit.js",encoding="utf-8").read()
findings=collections.OrderedDict()

def run(pg, screen):
    res = pg.evaluate(AUDIT)
    findings[screen] = res
    pg.screenshot(path=f"{SCR}/audit/{screen}.png", full_page=True)
    print("%-22s %d finding(s)" % (screen, len(res)))

with sync_playwright() as p:
    br=p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",args=["--no-sandbox"])
    ctx=br.new_context(viewport={"width":390,"height":844},device_scale_factor=2,locale="he-IL")
    pg=ctx.new_page()
    def tap(k,w=2600):
        pg.click(f'.st-key-{k} button'); pg.wait_for_timeout(w)

    pg.goto("http://localhost:8501/",wait_until="load",timeout=60000); pg.wait_for_timeout(6500)
    run(pg,"01_name_gate")

    pg.fill('input[type="text"]',"שלו")
    pg.locator('label:has(input[type="checkbox"])').first.click(); pg.wait_for_timeout(500)
    pg.get_by_role("button",name="המשך").click(); pg.wait_for_timeout(3500)
    run(pg,"02_role_picker")

    pg.get_by_role("button",name="כניסת חיילים").click(); pg.wait_for_timeout(4500)
    run(pg,"03_chat_home")

    tap("drawer_open_btn",2000); run(pg,"04_drawer")
    tap("open_settings",3200); run(pg,"05_settings_hub")

    for key, screen in [("nav_personal","06_personal"),("nav_language","07_language"),
                        ("nav_access","08_text_size"),("nav_privacy","09_privacy"),
                        ("nav_about","10_about"),("nav_policy_about","11_policy"),
                        ("nav_a11y","12_a11y"),("nav_contact","13_contact")]:
        try:
            tap(key,2800); run(pg,screen)
        except Exception as e:
            print("%-22s SKIP (%s)" % (screen, str(e).split("\n")[0][:60]))
        # back to the hub for the next one
        pg.reload(wait_until="load"); pg.wait_for_timeout(5000)
        tap("drawer_open_btn",1800); tap("open_settings",3000)

    br.close()

io.open(f"{SCR}/audit/findings.json","w",encoding="utf-8").write(
    json.dumps(findings,ensure_ascii=False,indent=1))
tot=sum(len(v) for v in findings.values())
print("\nTOTAL %d findings across %d screens" % (tot,len(findings)))
by=collections.Counter(f["k"] for v in findings.values() for f in v)
for k,n in by.most_common(): print("  %-18s %d" % (k,n))
