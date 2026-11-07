"""Live check: the YouTube widget IS a CRT cabinet, and the cabinet's own bezel
is its resize grip.

The card (app/static/crt/player.html) draws a 0.600 x 0.330 m TV edge to edge,
so the widget must stay at 1.8182:1 and the tube must sit inside the frame
(16:9, centred) at every size. The channel change is the product: a tune must
put snow on the tube, spin the dial, swap the player, flash the tube on and
show the OSD — and a second tune must do it again.

Requires the live stack: html-notes (:8035). Run against the deployed app.
Run: .venv/bin/python scripts/youtube_crt_check.py
"""
import atexit
import os
import re
import sys
import time
from playwright.sync_api import sync_playwright

HOST = os.environ.get("HN_HOST", "http://10.0.0.16:8035")
WIDGET_ID = "crt-live-check"
CABINET_ASPECT = 0.6 / 0.33           # 1.8182 — the widget's height follows it
VIDEO_A, VIDEO_B = "dQw4w9WgXcQ", "9bZkp7q19f0"

results, failures = [], 0


def check(name, ok, detail=""):
    global failures
    results.append((ok, name, detail))
    if not ok:
        failures += 1


INJECT = """
(a) => {
  const id = a.id, va = a.video;
  document.querySelectorAll('#' + id).forEach(n => n.remove());
  try { const s = JSON.parse(localStorage.getItem('widget_sizes') || '{}'); delete s[id];
        localStorage.setItem('widget_sizes', JSON.stringify(s)); } catch (e) {}
  const grid = document.querySelector('#dashboard-grid');
  const el = document.createElement('div');
  el.id = id;
  el.className = 'widget-container youtube-player col-span-2 relative overflow-hidden text-white flex flex-col group';
  el.setAttribute('x-data', `youtubePlayerWidget(${JSON.stringify(va)}, "Test Channel")`);
  el.setAttribute(':style', "{ height: tvHeight + 'px' }");
  el.innerHTML = `
    <iframe x-ref="crt" :src="'/static/crt/player.html?v=1'" class="w-full h-full border-none block" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen title="YouTube on a CRT"></iframe>
    <div class="absolute top-2 left-2 z-20 max-w-[60%] truncate bg-black/60 text-emerald-300 font-mono text-[11px] px-2 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" x-text="title"></div>
    <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn absolute top-2 right-2 z-20 text-white/60 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity">
      <span class="material-symbols-outlined text-[1.1rem]">close</span>
    </button>`;
  grid.appendChild(el);
  window.Alpine.initTree(el);
  window.__crt = window.Alpine.$data(el);
  if (window.__masonryLayout) window.__masonryLayout();
  return !!window.__crt;
}
"""

MEASURE = """
(id) => {
  const el = document.getElementById(id);
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const after = getComputedStyle(el, '::after');
  const ifr = el.querySelector('iframe');
  const ics = ifr ? getComputedStyle(ifr) : null;
  const saved = (JSON.parse(localStorage.getItem('widget_sizes') || '{}'))[id] || null;
  return {
    w: r.width, h: r.height, ratio: r.width / r.height,
    gridColumn: el.style.gridColumn || '',
    inlineHeight: el.style.height || '',
    radius: cs.borderTopLeftRadius,
    borderWidth: cs.borderTopWidth,
    bgImage: cs.backgroundImage,
    boxShadow: cs.boxShadow,
    afterContent: after.content,
    iframeBorder: ics ? ics.borderTopWidth : '',
    iframeBg: ics ? ics.backgroundColor : '',
    handle: !!el.querySelector(':scope > .widget-resize-handle'),
    bezelClass: el.classList.contains('is-resizing-bezel'),
    saved,
    iframes: el.querySelectorAll('iframe').length,
    src: (ifr || {}).src || '',
  };
}
"""

CARD = """
() => {
  const mv = document.getElementById('mv');
  const screen = document.getElementById('screen');
  const sr = screen ? screen.getBoundingClientRect() : null;
  const yt = document.getElementById('yt');
  const st = document.getElementById('static');
  const stc = st ? getComputedStyle(st) : null;
  const osd = document.getElementById('osd');
  const nosig = document.getElementById('nosig');
  return {
    loaded: !!(mv && mv.loaded),
    cardW: document.getElementById('card').getBoundingClientRect().width,
    cardH: document.getElementById('card').getBoundingClientRect().height,
    screen: sr ? {x: sr.x, y: sr.y, w: sr.width, h: sr.height} : null,
    screenRatio: sr ? sr.width / sr.height : 0,
    ytSrc: yt ? (yt.src || '') : '',
    ytVisible: yt ? (getComputedStyle(yt).visibility !== 'hidden' && getComputedStyle(yt).opacity !== '0' && getComputedStyle(yt).display !== 'none') : false,
    staticOn: stc ? (stc.opacity !== '0' && stc.display !== 'none') : false,
    osdText: osd ? osd.textContent : '',
    osdOn: osd ? osd.classList.contains('on') : false,
    nosigOn: nosig ? nosig.classList.contains('on') : false,
    nosigMsg: nosig ? (nosig.querySelector('.msg') || {}).textContent : '',
    nosigLink: nosig ? (nosig.querySelector('a') || {}).className : '',
  };
}
"""

KNOB = """
(name) => {
  const mv = document.getElementById('mv');
  const sym = Object.getOwnPropertySymbols(mv).find(s => String(s).includes('scene'));
  const scene = sym ? mv[sym] : null;
  let node = null;
  if (scene && scene.getObjectByName) node = scene.getObjectByName(name);
  return node ? node.rotation.z : null;
}
"""


def card_frame(pg, timeout_ms=25000):
    import time
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for f in pg.frames:
            if "crt/player.html" in (f.url or ""):
                return f
        pg.wait_for_timeout(200)
    return None


def report():
    for ok, name, detail in results:
        print(f"{'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))
    print(f"\n{len(results) - failures}/{len(results)} checks passed")


atexit.register(report)   # a crash mid-run still prints what did pass


with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                                "--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(HOST, wait_until="domcontentloaded")
    pg.wait_for_function("() => window.Alpine && window.WidgetResizer")
    pg.evaluate(INJECT, {"id": WIDGET_ID, "video": VIDEO_A})
    fr = card_frame(pg)
    check("the CRT card loads inside the widget", fr is not None)
    if fr is None:
        print("\n".join(f"{'✓' if ok else '✗'} {n} {d}" for ok, n, d in results))
        sys.exit(1)
    fr.wait_for_function("() => document.getElementById('mv') && document.getElementById('mv').loaded",
                         timeout=30000)

    # ── the box is the cabinet ──────────────────────────────────────────────
    m = pg.evaluate(MEASURE, WIDGET_ID)
    check("the widget is the cabinet's 1.8182:1", abs(m["ratio"] - CABINET_ASPECT) < 0.02,
          f"{m['w']:.0f}x{m['h']:.0f} ratio {m['ratio']:.4f}")
    check("no corner grip on the cabinet", not m["handle"])
    check("no holographic frame or border drawn around it",
          m["radius"] in ("0px", "") and m["borderWidth"] in ("0px", "")
          and m["bgImage"] == "none" and m["boxShadow"] == "none"
          and m["afterContent"] in ("none", ""),
          f"radius={m['radius']} border={m['borderWidth']} bg={m['bgImage'][:24]} after={m['afterContent']}")
    check("the card iframe has no border or panel background",
          m["iframeBorder"] in ("0px", "") and m["iframeBg"] in ("rgba(0, 0, 0, 0)", "transparent"),
          f"border={m['iframeBorder']} bg={m['iframeBg']}")
    check("one iframe: the card owns the player", m["iframes"] == 1, str(m["iframes"]))
    check("the iframe is the CRT card", "crt/player.html" in m["src"], m["src"])

    # ── the tube sits inside the frame, 16:9 ────────────────────────────────
    c = fr.evaluate(CARD)
    check("the GLB is loaded in the card", c["loaded"])
    check("the tube is 16:9", abs(c["screenRatio"] - 16 / 9) < 0.06, f"{c['screenRatio']:.3f}")
    check("the tube sits inside the frame",
          c["screen"]["x"] > 0 and c["screen"]["y"] > 0
          and c["screen"]["x"] + c["screen"]["w"] < c["cardW"]
          and c["screen"]["y"] + c["screen"]["h"] < c["cardH"],
          f"screen {c['screen']} in card {c['cardW']:.0f}x{c['cardH']:.0f}")

    # ── first tune: static, then the player, then the OSD ───────────────────
    fr.wait_for_function("() => document.getElementById('yt') && document.getElementById('yt').src.includes('embed')",
                         timeout=25000)
    c = fr.evaluate(CARD)
    check("the tuned player carries the video id", VIDEO_A in c["ytSrc"], c["ytSrc"][:80])
    # The OSD is a 3 s flash on reveal, so it must be caught, not sampled late.
    try:
        fr.wait_for_function("() => { const o = document.getElementById('osd');"
                             " return o.classList.contains('on') && /CH \\d\\d/.test(o.textContent); }",
                             timeout=25000)
        check("the OSD names the channel", True)
    except Exception:
        c = fr.evaluate(CARD)
        check("the OSD names the channel", False, f"{c['osdText']} on={c['osdOn']}")

    # ── channel change: snow first, then the new channel ────────────────────
    pg.evaluate("(v) => window.__crt.playById(v)", VIDEO_B)
    saw_static = False
    for _ in range(40):
        st = fr.evaluate("() => { const s = document.getElementById('static'); const c = getComputedStyle(s); return c.opacity !== '0' && c.display !== 'none'; }")
        if st:
            saw_static = True
            break
        pg.wait_for_timeout(50)
    check("changing the channel puts snow on the tube", saw_static)
    fr.wait_for_function("(v) => { const y = document.getElementById('yt'); return y && y.src.includes(v); }",
                         arg=VIDEO_B, timeout=25000)
    c = fr.evaluate(CARD)
    check("the new channel is playing", VIDEO_B in c["ytSrc"], c["ytSrc"][:80])
    # On a live page a blocked embed walks the candidate list, so the tube can
    # land past the channel that was asked for, and each new tune restarts the
    # 3 s OSD. Poll until the OSD, the dial and the widget's own tune counter
    # agree — that is the invariant, not a fixed channel number.
    import re
    agreed = False
    last = ""
    deadline = time.time() + 60
    while time.time() < deadline:
        c = fr.evaluate(CARD)
        tuned = pg.evaluate("() => window.__crt.attemptedIds.length - 1")
        m = re.search(r"CH (\d+)", c["osdText"] or "")
        if c["osdOn"] and m and int(m.group(1)) == tuned:
            ang = fr.evaluate(KNOB, "TV_CH_KNOB")
            if ang is not None and abs(ang - tuned * 3.14159265 / 6) < 0.06:
                agreed = True
                break
            last = f"dial {ang} for CH {tuned}"
        else:
            last = f"OSD {c['osdText']!r} on={c['osdOn']} vs tuned {tuned}"
        pg.wait_for_timeout(200)
    check("the OSD, the dial and the tune counter agree", agreed, last)

    # ── a dead channel: NO SIGNAL + the watch link ──────────────────────────
    pg.evaluate("() => { window.__crt.watchUrl = 'https://www.youtube.com/watch?v=x';"
                " window.__crt.setError('This video blocks embedding.'); }")
    fr.wait_for_function("() => document.getElementById('nosig').classList.contains('on')", timeout=5000)
    c = fr.evaluate(CARD)
    check("a dead channel shows NO SIGNAL", c["nosigOn"], c["nosigMsg"])
    check("the no-signal screen offers the video's page", "on" in c["nosigLink"], c["nosigLink"])

    # ── bezel drag: the cabinet's own bezel is the grip ─────────────────────
    box = fr.evaluate("() => { const r = document.getElementById('mv').getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; }")
    sx, sy = box["x"] + box["w"] - 8, box["y"] + box["h"] / 2
    pg.mouse.move(sx, sy)
    pg.mouse.down()
    for i in range(1, 7):
        pg.mouse.move(sx + 340 * i / 6, sy)
        pg.wait_for_timeout(30)
    pg.mouse.up()
    pg.wait_for_timeout(300)
    m = pg.evaluate(MEASURE, WIDGET_ID)
    check("dragging the bezel widens the widget", m["gridColumn"].startswith("span ") and m["gridColumn"] != "span 2",
          m["gridColumn"])
    check("the saved size is columns only", m["saved"] is not None and set(m["saved"]) == {"cols"},
          str(m["saved"]))
    check("the widened widget is still 1.8182:1", abs(m["ratio"] - CABINET_ASPECT) < 0.02,
          f"{m['w']:.0f}x{m['h']:.0f} ratio {m['ratio']:.4f}")
    check("the drag leaves no outline", not m["bezelClass"])
    fr.wait_for_timeout(400)
    c = fr.evaluate(CARD)
    check("the tube still fits the wider frame",
          c["screen"]["x"] > 0 and c["screen"]["x"] + c["screen"]["w"] < c["cardW"],
          f"screen {c['screen']} in card {c['cardW']:.0f}")

    # ── double-click resets ────────────────────────────────────────────────
    fr.dblclick("#mv", position={"x": box["w"] * 0.05, "y": box["h"] * 0.95})
    pg.wait_for_timeout(500)
    m = pg.evaluate(MEASURE, WIDGET_ID)
    check("double-clicking the cabinet drops the saved size",
          m["gridColumn"] == "" and m["saved"] is None, f"grid={m['gridColumn']} saved={m['saved']}")
    check("…and the height is still the derived one",
          m["inlineHeight"].endswith("px") and abs(m["h"] - round(m["w"] / CABINET_ASPECT)) <= 2,
          f"inline={m['inlineHeight']} {m['w']:.0f}x{m['h']:.0f}")
    check("…still 1.8182:1", abs(m["ratio"] - CABINET_ASPECT) < 0.02, f"{m['ratio']:.4f}")

    b.close()

report()
sys.exit(1 if failures else 0)
