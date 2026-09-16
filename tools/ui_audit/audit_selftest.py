import io
from playwright.sync_api import sync_playwright
SCR="/tmp/claude-0/-home-user-command-ai/80d92365-c2e7-5519-96f9-57252960b5f8/scratchpad"
AUDIT=io.open(f"{SCR}/audit.js",encoding="utf-8").read()

INJECT = """() => {
  const mk = (css, html, tag='div') => {
    const e = document.createElement(tag);
    e.style.cssText = css; e.innerHTML = html;
    document.body.appendChild(e); return e;
  };
  // 1 low contrast: #3A3F30 text on the #171A12 page
  mk('color:#3A3F30;background:#171A12;font-size:14px;padding:8px','BAIT-contrast');
  // 2 text clipped by its own box
  mk('width:60px;overflow:hidden;white-space:nowrap;color:#fff;background:#111;font-size:14px','BAIT-clipped-this-text-is-far-too-long-for-sixty-pixels');
  // 3 element sticking out past the right edge
  mk('position:absolute;left:'+(innerWidth-20)+'px;top:300px;width:200px;height:30px;background:#333;color:#fff','BAIT-offscreen');
  // 4 a 20px-tall button
  mk('height:20px;background:#444;color:#fff;border:0','BAIT-small','button');
  // 5 a button nobody can name
  mk('height:60px;width:60px;background:#444;border:0','','button');
  // 6 contrast hidden behind a translucent overlay (the v1 blind spot, inverted):
  //    light text on a 4.5% white wash over near-black is still LOW contrast
  const box = mk('background:rgba(239,240,232,.045);padding:10px');
  const t = document.createElement('div');
  t.style.cssText='color:#2B2F24;font-size:13px'; t.textContent='BAIT-translucent';
  box.appendChild(t);
}"""

with sync_playwright() as p:
    br=p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",args=["--no-sandbox"])
    pg=br.new_context(viewport={"width":390,"height":844},locale="he-IL").new_page()
    pg.goto("http://localhost:8501/",wait_until="load",timeout=60000); pg.wait_for_timeout(6500)
    clean = pg.evaluate(AUDIT)
    print("before injection: %d finding(s)" % len(clean))
    pg.evaluate(INJECT); pg.wait_for_timeout(400)
    res = pg.evaluate(AUDIT)
    got = [f for f in res if "BAIT" in (f.get("t") or "") or f["k"]=="unnamed-control"]
    want = {"contrast","clipped-text","off-screen","small-target","unnamed-control"}
    seen = {f["k"] for f in got}
    for f in got: print("  CAUGHT [%s] %s | %s" % (f["k"], f["what"], f.get("t","")))
    missing = want - seen
    print("\nkinds caught : %s" % ", ".join(sorted(seen)))
    print("kinds MISSED : %s" % (", ".join(sorted(missing)) or "none"))
    br.close()
