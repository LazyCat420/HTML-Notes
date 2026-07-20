"""Push rendered card HTML through the REAL DOMPurify build and the REAL canvas
config, then assert links are still clickable in a live browser."""
import re, sys
from playwright.sync_api import sync_playwright

OUT = "/tmp/claude-1000/-home-lazycat-github-projects-sun/d6eeae79-b23c-4682-96f5-177348115221/scratchpad"
ROOT = "/home/lazycat/github/projects/sun/HTML-Notes/app/static"

card = open(f"{OUT}/card_links.html").read()
# Pull the canvas config out of index.js so this tests what SHIPS, not a copy.
js = open(f"{ROOT}/index.js").read()
m = re.search(r"const CANVAS_DOMPURIFY_CONFIG\s*=\s*(\{.*?\});", js, re.S)
cfg = m.group(1)
print("using config from index.js:", " ".join(cfg.split())[:120])

page = f"""<!doctype html><html><head><script src="file://{ROOT}/lib/dompurify.min.js"></script>
</head><body><div id="live-canvas"></div><script>
const CANVAS_DOMPURIFY_CONFIG = {cfg};
const raw = {card!r};
document.getElementById('live-canvas').innerHTML =
    DOMPurify.sanitize(raw, CANVAS_DOMPURIFY_CONFIG);
</script></body></html>"""
open(f"{OUT}/links_sanitized.html", "w").write(page)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto(f"file://{OUT}/links_sanitized.html")
    pg.wait_for_timeout(800)
    links = pg.evaluate("""() => [...document.querySelectorAll('#live-canvas a')].map(a => ({
        text: a.textContent.trim().slice(0, 40),
        href: a.getAttribute('href'),
        target: a.getAttribute('target'),
        rel: a.getAttribute('rel'),
        clickable: a.getBoundingClientRect().width > 0 &&
                   getComputedStyle(a).pointerEvents !== 'none',
    }))""")
    b.close()

fails = []
if not links:
    fails.append("DOMPurify stripped ALL anchors")
for l in links:
    print(f"  {l['text']:42} href={l['href']} target={l['target']} rel={l['rel']} clickable={l['clickable']}")
    if not l["href"]:
        fails.append(f"{l['text']}: href stripped")
    if l["target"] != "_blank":
        fails.append(f"{l['text']}: target={l['target']} (would hijack the tab)")
    if not l["clickable"]:
        fails.append(f"{l['text']}: not clickable")
if not any("example.com" in (l["href"] or "") for l in links):
    fails.append("bare URL was not autolinked")

print("\n" + ("FAIL: " + "; ".join(fails) if fails else "PASS — links survive sanitize and are clickable"))
sys.exit(1 if fails else 0)
