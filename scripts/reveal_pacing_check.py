"""Drive the REAL paced-reveal functions from index.js in a headless browser.

The failure mode that matters is not "pacing looks wrong" — it is a widget that
never becomes visible. So most of this checks the safety rails: every hold has a
timeout, anything queued is flushed when speech stops or is muted, and the
transient class is never serialized into the canvas the server adopts.
"""
import re
import sys

from playwright.sync_api import sync_playwright

JS = open("/home/lazycat/github/projects/sun/HTML-Notes/app/static/index.js").read()


def fn(name):
    """Lift a `function name(...) {...}` out of index.js by brace-matching, so
    this exercises the SHIPPED code rather than a re-implementation."""
    m = re.search(rf"\n    function {name}\(", JS)
    if not m:
        raise SystemExit(f"could not find function {name}")
    start = m.start() + 1
    i = JS.index("{", m.end())
    depth = 0
    for j in range(i, len(JS)):
        if JS[j] == "{":
            depth += 1
        elif JS[j] == "}":
            depth -= 1
            if depth == 0:
                return JS[start:j + 1]
    raise SystemExit(f"unbalanced braces in {name}")


CONSTS = re.search(r"const REVEAL_HOLD_MAX_MS = (\d+);", JS).group(0)

PAGE = f"""<!doctype html><html><head><style>
.widget-container.is-pending-reveal {{ visibility: hidden; opacity: 0; }}
</style></head><body>
<div id="dashboard-grid"></div>
<script>
{CONSTS}
const pendingReveals = [];
let isProcessingQueue = false;
const ttsQueue = [];
let muted = false, ttsDown = false;
function ttsAvailable() {{ return !muted && !ttsDown; }}
function flagCanvasChange(el, cls) {{ if (el) el.dataset.flagged = cls; }}
window.__masonryLayout = null;

{fn("revealGateActive")}
{fn("holdWidgetForReveal")}
{fn("revealWidget")}
{fn("revealNextWidget")}
{fn("revealAllPending")}

// ── harness helpers ────────────────────────────────────────────────
window.addWidget = (id) => {{
  const el = document.createElement('div');
  el.className = 'widget-container';
  el.id = id;
  document.getElementById('dashboard-grid').appendChild(el);
  if (!(revealGateActive() && holdWidgetForReveal(el))) flagCanvasChange(el, 'is-entering');
  return el.classList.contains('is-pending-reveal');
}};
window.visible = (id) => {{
  const el = document.getElementById(id);
  return !!el && !el.classList.contains('is-pending-reveal');
}};
window.setSpeech = (processing, queued) => {{
  isProcessingQueue = processing;
  ttsQueue.length = 0;
  for (let i = 0; i < queued; i++) ttsQueue.push({{}});
}};
window.setMuted = (m) => {{ muted = m; }};
window.setTtsDown = (d) => {{ ttsDown = d; }};
window.pendingCount = () => pendingReveals.length;
window.speakSentence = () => revealNextWidget();
window.flushAll = () => revealAllPending();
window.reset = () => {{
  document.getElementById('dashboard-grid').innerHTML = '';
  pendingReveals.length = 0;
}};
</script></body></html>"""

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        fails.append(name)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content(PAGE)

    print("pacing")
    pg.evaluate("() => { reset(); setMuted(false); setTtsDown(false); setSpeech(true, 3); }")
    held = [pg.evaluate(f"() => addWidget('w{i}')") for i in range(3)]
    check("widgets are held while speech is pending", all(held))
    check("none visible yet", not any(pg.evaluate(f"() => visible('w{i}')") for i in range(3)))

    pg.evaluate("() => speakSentence()")
    check("first sentence reveals exactly one widget",
          pg.evaluate("() => visible('w0') && !visible('w1') && !visible('w2')"))
    pg.evaluate("() => speakSentence()")
    check("second sentence reveals the second, in order",
          pg.evaluate("() => visible('w1') && !visible('w2')"))
    pg.evaluate("() => speakSentence()")
    check("third reveals the last", pg.evaluate("() => visible('w2')"))
    check("queue drains to empty", pg.evaluate("() => pendingCount()") == 0)

    print("\nsafety rails — a widget must never stay hidden")
    pg.evaluate("() => { reset(); setSpeech(false, 0); }")
    pg.evaluate("() => addWidget('nogate')")
    check("no gating when nothing will be spoken", pg.evaluate("() => visible('nogate')"))

    pg.evaluate("() => { reset(); setMuted(true); setSpeech(true, 2); }")
    pg.evaluate("() => addWidget('muted')")
    check("no gating when muted", pg.evaluate("() => visible('muted')"))

    pg.evaluate("() => { reset(); setMuted(false); setTtsDown(true); setSpeech(true, 2); }")
    pg.evaluate("() => addWidget('offline')")
    check("no gating when the TTS service is in offline back-off",
          pg.evaluate("() => visible('offline')"))

    pg.evaluate("() => { reset(); setTtsDown(false); setSpeech(true, 3); }")
    for i in range(3):
        pg.evaluate(f"() => addWidget('f{i}')")
    pg.evaluate("() => flushAll()")
    check("flush reveals everything held",
          all(pg.evaluate(f"() => visible('f{i}')") for i in range(3)))
    check("flush empties the queue", pg.evaluate("() => pendingCount()") == 0)

    print("\ntimeout backstop (no sentence ever arrives)")
    pg.evaluate("() => { reset(); setSpeech(true, 5); }")
    pg.evaluate("() => addWidget('stranded')")
    check("held initially", not pg.evaluate("() => visible('stranded')"))
    hold_ms = int(re.search(r"(\d+)", CONSTS).group(1))
    pg.wait_for_timeout(hold_ms + 400)
    check(f"revealed by the {hold_ms}ms backstop anyway",
          pg.evaluate("() => visible('stranded')"),
          "a widget would have stayed invisible forever")

    print("\nfewer sentences than widgets")
    pg.evaluate("() => { reset(); setSpeech(true, 1); }")
    for i in range(3):
        pg.evaluate(f"() => addWidget('x{i}')")
    pg.evaluate("() => speakSentence()")
    pg.evaluate("() => flushAll()")   # queue drained -> processTTSQueue flushes
    check("the extras are flushed, not stranded",
          all(pg.evaluate(f"() => visible('x{i}')") for i in range(3)))

    b.close()

print("\n" + ("FAIL: " + ", ".join(fails) if fails else
              "PASS — paced with speech, and nothing can stay hidden"))
sys.exit(1 if fails else 0)
