"""Check the TTS narration filter against REAL observed sentences.

Lifts the regexes out of index.js so this tests what ships, not a copy.
The risk with a filter like this is over-reach: it must drop process commentary
without ever swallowing a real answer.
"""
import re, sys
from playwright.sync_api import sync_playwright

JS = open("/home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js").read()
def block(name):
    m = re.search(rf"const {name} =\s*(/.+?/i);", JS, re.S)
    if not m: raise SystemExit(f"could not find {name}")
    return f"const {name} = {m.group(1)};"

PAGE = f"""<!doctype html><html><body><script>
{block('TTS_NARRATION_RE')}
{block('TTS_TOOL_TALK_RE')}
window.isNarration = (t) => {{
  const s = (t||'').trim();
  if (!s) return true;
  return TTS_NARRATION_RE.test(s) || TTS_TOOL_TALK_RE.test(s);
}};
</script></body></html>"""

# (sentence, should_be_dropped)
CASES = [
    # Observed verbatim in the live session — must be dropped.
    ("The Google RSS URLs can't be fetched directly — they're RSS feeds, not regular pages.", False),
    ("I'll search for the actual article URLs from the source outlets instead.", True),
    ("Added it to your canvas.", True),
    ("Let me build a data_card with the best sandals.", True),
    ("Now I have comprehensive data from three major review sources.", True),
    ("I'll add a widget for that.", True),
    ("I've added a data_card showing the results.", True),
    # Real answers — must NEVER be dropped.
    ("Live traffic for Oakland.", False),
    ("Here's the forecast for Tokyo, JP.", False),
    ("The Seattle Rain Hat is the most waterproof, using Gore-Tex.", False),
    ("Found 3, starting with US and Iran tensions escalate.", False),
    ("Traffic to San Jose is clear, about 35 minutes via 880.", False),
    ("Fever the Ghost's SOURCE is playing.", False),
    ("Apple closed at 214 dollars, up 1.2 percent.", False),
    ("It will rain in Tokyo tomorrow afternoon.", False),
    ("Sunset Rosin has the best reviews in the city.", False),
]

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); pg.set_content(PAGE)
    fails = []
    for text, should_drop in CASES:
        got = pg.evaluate("(t) => window.isNarration(t)", text)
        mark = "drop" if got else "SPEAK"
        ok = (got == should_drop)
        if not ok:
            fails.append(f"{'over-reach' if got else 'leaked narration'}: {text!r}")
        print(f"  {'ok ' if ok else 'FAIL'} [{mark:5}] {text[:74]}")
    b.close()

print("\n" + ("FAIL: " + "; ".join(fails) if fails else
              "PASS — narration dropped, every real answer still spoken"))
sys.exit(1 if fails else 0)
