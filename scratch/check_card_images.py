"""Drive a real browser at the live canvas and report what the research card
actually rendered — images, their sources, and whether they LOAD.

API-level success has hidden a canvas that never painted before, and an <img>
with a dead src looks the same in the DOM as a good one. So this checks the
rendered DOM and then each image's naturalWidth, which is only non-zero once
the bytes actually arrived.
"""
import asyncio
import sys

from playwright.async_api import async_playwright

URL = "http://10.0.0.16:8035/"
ASK = sys.argv[1] if len(sys.argv) > 1 else "best espresso machines under $500"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1400, "height": 1000})
        await page.goto(URL, wait_until="networkidle")

        box = page.locator("#message-input, textarea, input[type=text]").first
        await box.fill(ASK)
        await box.press("Enter")

        # Research turns run ~30s; poll for a widget rather than sleeping blind.
        widget = page.locator("[data-sig]").first
        try:
            await widget.wait_for(timeout=180_000)
        except Exception:
            print("NO WIDGET RENDERED within 180s")
            await page.screenshot(path="scratch/card_none.png", full_page=True)
            await browser.close()
            return

        # Let the card settle: the answer streams in, then thumbs load.
        await page.wait_for_timeout(6000)

        report = await page.evaluate("""() => {
            const w = document.querySelector('[data-sig]');
            if (!w) return {error: 'no widget'};
            const imgs = [...w.querySelectorAll('img')].map(i => ({
                src: i.getAttribute('src') || '',
                loaded: i.naturalWidth > 0,
                w: i.naturalWidth, h: i.naturalHeight,
                cls: i.className.slice(0, 40),
            }));
            const monograms = w.querySelectorAll('.item-thumb.bg-gradient-to-tr').length;
            const links = [...w.querySelectorAll('a[href^="http"]')].map(a => a.getAttribute('href'));
            return {
                id: w.id,
                imgs,
                monograms,
                links: links.slice(0, 8),
                textLen: (w.innerText || '').length,
            };
        }""")

        if report.get("error"):
            print("ERROR:", report["error"])
        else:
            imgs = report["imgs"]
            ok = [i for i in imgs if i["loaded"]]
            print(f"widget id      : {report['id']}")
            print(f"text length    : {report['textLen']} chars")
            print(f"<img> tags     : {len(imgs)}")
            print(f"actually LOADED: {len(ok)}")
            print(f"monogram tiles : {report['monograms']} (grey fallback = no image)")
            for i in imgs:
                mark = "OK " if i["loaded"] else "DEAD"
                print(f"   [{mark}] {i['w']}x{i['h']} {i['src'][:78]}")
            print(f"source links   : {len(report['links'])}")
            for l in report["links"][:6]:
                print(f"   {l[:78]}")

        await page.screenshot(path="scratch/card_images.png", full_page=True)
        print("screenshot -> scratch/card_images.png")
        await browser.close()


asyncio.run(main())
