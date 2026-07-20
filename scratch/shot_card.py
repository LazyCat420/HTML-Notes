"""Render data_card variants at controlled widths and assert the figure layout.

Each card is rendered in its own fixed-width box (no dashboard grid) so the only
variable is card width — the thing the container query keys off. Images are data
URIs with awkward aspect ratios, since those are the shapes that broke under the
old fixed-height hero + object-cover.

Checks, per card:
  * is any part of the image cropped away (rendered content vs natural ratio)
  * does the figure overlap the text it is supposed to sit beside
  * does the figure float (wide) or stack (narrow)
"""
import base64
import io
import sys

sys.path.insert(0, "/home/lazycat/github/projects/sun/HTML-Notes")

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from app.widgets.factory import render_data_card

OUT = "/tmp/claude-1000/-home-lazycat-github-projects-sun/d6eeae79-b23c-4682-96f5-177348115221/scratchpad"
CSS = "/home/lazycat/github/projects/sun/HTML-Notes/app/static/index.css"


def img_uri(w, h, label):
    """White-background 'product shot' with the subject low in the frame — the
    composition that lost its crown to object-top cropping. The red border must
    survive on all four sides, or something cropped it."""
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.ellipse([w * 0.15, h * 0.55, w * 0.85, h * 0.95], fill=(20, 24, 40))
    d.rectangle([2, 2, w - 3, h - 3], outline=(220, 30, 30), width=4)
    d.text((10, 10), label, fill=(180, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


ANSWER = (
    "A selection of highly-rated waterproof hats for rain, hiking and commuting. "
    "The Outdoor Research Seattle Rain Hat uses advanced Gore-Tex waterproofing and "
    "is breathable enough for professional-grade protection in heavy downpours.\n\n"
    "The Sunday Afternoons Ultra Adventure Storm rates 4.7/5 and suits general hiking. "
    "Sizing runs slightly large across the category, so size down if between sizes."
)

# (id, label, card width, config)
CASES = [
    ("c1", "answer + portrait 600x900 @ 720px", 720, {
        "title": "Best Waterproof Hats for Rain, Hiking, and Commuting",
        "subtitle": "A selection of highly-rated waterproof hats",
        "image": img_uri(600, 900, "PORTRAIT"), "answer": ANSWER,
        "items": [{"title": "Outdoor Research Seattle Rain Hat", "url": "https://rei.com"}],
    }),
    ("c2", "answer + panorama 1600x400 @ 720px", 720, {
        "title": "Panorama", "image": img_uri(1600, 400, "WIDE"), "answer": ANSWER}),
    ("c3", "answer + square @ 340px (narrow)", 340, {
        "title": "Narrow card", "image": img_uri(700, 700, "SQUARE"), "answer": ANSWER}),
    ("c4", "items-only + square @ 720px", 720, {
        "title": "Wide list card", "image": img_uri(700, 700, "SQUARE"),
        "items": [{"title": f"Result number {i}", "description": "Supporting description text that runs on a while and wraps.",
                   "url": "https://example.com"} for i in range(5)]}),
]

blocks = "".join(
    f'<p style="color:#7dd3fc;font:12px monospace;margin:18px 0 6px">{lbl}</p>'
    f'<div style="width:{w}px">{render_data_card(cid, cfg)}</div>'
    for cid, lbl, w, cfg in CASES
)

PAGE = f"""<!doctype html><html><head>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="file://{CSS}">
<style>
body{{background:#0f172a;padding:20px;font-family:system-ui}}
.answer-prose p{{margin:0 0 .6rem;font-size:.82rem;line-height:1.5;color:#cbd5e1}}
</style></head><body>{blocks}</body></html>"""

open(f"{OUT}/card.html", "w").write(PAGE)

MEASURE = """(cid) => {
  const card = document.getElementById(cid);
  const fig  = card.querySelector('.data-card-figure');
  const img  = fig.querySelector('img');
  const cs   = getComputedStyle(fig);
  const fb = fig.getBoundingClientRect(), cb = card.getBoundingClientRect();
  const ib = img.getBoundingClientRect();

  // With object-fit:contain the drawn content is scaled to FIT the box, so the
  // visible content is the natural image scaled by min(bw/nw, bh/nh). Anything
  // less than 1.0 coverage of either axis would mean pixels were discarded.
  const s = Math.min(ib.width / img.naturalWidth, ib.height / img.naturalHeight);
  const drawnW = img.naturalWidth * s, drawnH = img.naturalHeight * s;
  const cropped = (drawnW > ib.width + 0.5) || (drawnH > ib.height + 0.5);
  const letterbox = Math.round(ib.width - drawnW) + Math.round(ib.height - drawnH);

  // Does the figure overlap the text? Must compare against LINE boxes, not the
  // block box: a paragraph's block box legitimately extends underneath a float
  // while only its line boxes shorten. Range.getClientRects() gives line boxes.
  const rects = [];
  for (const el of card.querySelectorAll('.answer-prose p, .data-card-item')) {
    const r = document.createRange();
    r.selectNodeContents(el);
    rects.push(...r.getClientRects());
  }
  let overlap = 0;
  for (const tb of rects) {
    if (tb.width < 1 || tb.height < 1) continue;
    const ox = Math.min(fb.right, tb.right) - Math.max(fb.left, tb.left);
    const oy = Math.min(fb.bottom, tb.bottom) - Math.max(fb.top, tb.top);
    if (ox > 1 && oy > 1) overlap += Math.round(ox * oy);
  }
  return {
    cardW: Math.round(cb.width), float: cs.float,
    figW: Math.round(fb.width), figH: Math.round(fb.height),
    cropped, letterbox, overlap,
    rightInset: Math.round(cb.right - fb.right),
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 2400})
    pg.goto(f"file://{OUT}/card.html")
    pg.wait_for_timeout(2500)  # tailwind CDN JIT compile

    ok = True
    for cid, lbl, w, _ in CASES:
        m = pg.evaluate(MEASURE, cid)
        want_float = "right" if m["cardW"] >= 480 else "none"
        bad = []
        if m["cropped"]:
            bad.append("IMAGE CROPPED")
        if m["overlap"] > 0:
            bad.append(f"figure overlaps text ({m['overlap']}px²)")
        if m["float"] != want_float:
            bad.append(f"float={m['float']} expected {want_float}")
        ok &= not bad
        print(f"{'FAIL' if bad else 'pass'}  {lbl}")
        print(f"      card={m['cardW']}px figure={m['figW']}x{m['figH']} float={m['float']} "
              f"letterbox={m['letterbox']}px right-inset={m['rightInset']}px")
        for x in bad:
            print(f"      !! {x}")

    pg.screenshot(path=f"{OUT}/cards.png", full_page=True)
    b.close()

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
print("screenshot ->", f"{OUT}/cards.png")
