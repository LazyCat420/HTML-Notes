"""Measure how the glitch reveal READS: duration, and the wavefront's progress.

scripts/glitch_check.py proves correctness (text restores byte-identically, no
markup leaks). This proves LEGIBILITY — the reveal has to last long enough, and
resolve progressively, to look like text being reprinted rather than a flicker.

Samples the settled-character fraction over time. A progressive left-to-right
wipe shows that fraction climbing steadily; a flicker jumps 0 -> 1.
"""
import re
import sys

from playwright.sync_api import sync_playwright

JS = open("/home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js").read()


def const(name):
    """Lift a `const NAME = ...;` line out of index.js so this measures the
    SHIPPED values, not a copy that can drift."""
    m = re.search(rf"const {name} = (.+?);\n", JS)
    if not m:
        raise SystemExit(f"could not find {name} in index.js")
    return f"const {name} = {m.group(1)};"


CONSTS = "\n".join([const("GLITCH_MIN_MS"), const("GLITCH_MAX_MS"),
                    const("GLITCH_MS_PER_CHAR"), const("GLITCH_CHAR_LIFE")])

# The exact duration formula from index.js.
PAGE = f"""<!doctype html><html><body><script>
{CONSTS}
window.duration = (chars) => Math.max(GLITCH_MIN_MS,
    Math.min(GLITCH_MAX_MS, chars * GLITCH_MS_PER_CHAR));
window.knobs = {{GLITCH_MIN_MS, GLITCH_MAX_MS, GLITCH_MS_PER_CHAR, GLITCH_CHAR_LIFE}};

// Settled fraction at normalised time t, mirroring the per-character schedule:
//   charStart = (i/total) * (1 - CHAR_LIFE);  p = (t - charStart)/CHAR_LIFE
window.settledAt = (t, total) => {{
  let settled = 0;
  for (let i = 0; i < total; i++) {{
    const charStart = (i / total) * (1 - GLITCH_CHAR_LIFE);
    if ((t - charStart) / GLITCH_CHAR_LIFE >= 1) settled++;
  }}
  return settled / total;
}};
</script></body></html>"""

# Representative payloads: a short follow-up line, a normal answer, a full
# research card (~480 words, the size that motivated the feature).
CASES = [("short follow-up", 180), ("normal answer", 1200), ("research card", 2800)]

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content(PAGE)
    knobs = pg.evaluate("window.knobs")
    print(f"knobs: {knobs}\n")

    fails = []
    for name, chars in CASES:
        ms = pg.evaluate("(c) => window.duration(c)", chars)
        print(f"{name:16} {chars:5} chars -> {ms:6.0f}ms")

        # Progressive? Sample the settled fraction across the run.
        pts = [(round(t, 2), round(pg.evaluate(
            "([t, n]) => window.settledAt(t, n)", [t, 200]), 2))
            for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
        print(f"{'':16} settled: " + "  ".join(f"t={t}:{v:.0%}" for t, v in pts))

        # A reveal the eye can follow needs roughly a second minimum.
        if ms < 900:
            fails.append(f"{name}: {ms:.0f}ms is too fast to read as a print")
        # And it must not be so long it feels broken.
        if ms > 5000:
            fails.append(f"{name}: {ms:.0f}ms is long enough to feel stuck")

    # Monotonic, gradual progress — not a 0 -> 1 jump.
    vals = [pg.evaluate("([t, n]) => window.settledAt(t, n)", [t / 10, 200]) for t in range(11)]
    if any(b_ < a_ - 1e-9 for a_, b_ in zip(vals, vals[1:])):
        fails.append("settled fraction is not monotonic")
    mid = vals[5]
    if not (0.15 < mid < 0.85):
        fails.append(f"at the halfway point {mid:.0%} is settled — that's a flicker, "
                     "not a wavefront crossing the card")

    b.close()

print("\n" + ("FAIL: " + "; ".join(fails) if fails else
              "PASS — reveal is long enough to read and resolves progressively"))
sys.exit(1 if fails else 0)
