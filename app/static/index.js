// ─── HN: browser-console debug logger ─────────────────────────────
// Prints every turn's routing + render decisions to the Chrome DevTools
// console so "the result was in the wrong format" is diagnosable on the spot:
// the query, the server's route breadcrumb (fast-path vs agent, which widget),
// each status line, tool calls, and the actual widget types that got rendered.
// Toggle off with `localStorage.setItem('hn_debug','0')` in the console.
window.HN = (function () {
    const on = () => localStorage.getItem('hn_debug') !== '0';
    const S = 'color:#8b5cf6;font-weight:bold';
    // Pull the widget types out of a rendered canvas so a misrender is obvious.
    function widgetTypes(html) {
        const out = [];
        try {
            const tmp = document.createElement('div');
            tmp.innerHTML = html || '';
            tmp.querySelectorAll('.widget-container, .glass-card, .rendered-component').forEach(el => {
                // data-widget-type is the authoritative stamp (set by
                // generate_widget_html on every widget root); the class
                // heuristic below misreported 12 of the factory types (they
                // carry no '-widget' class) as an icon glyph or a layout class.
                const stamped = el.getAttribute('data-widget-type');
                if (stamped) { out.push(stamped); return; }
                const cls = [...el.classList].find(c => c.endsWith('-widget'));
                const icon = el.querySelector('.material-symbols-outlined, .text-xl');
                out.push(cls || icon?.textContent?.trim() || el.className.split(' ')[1] || 'widget');
            });
        } catch (e) { /* best-effort */ }
        return out;
    }
    return {
        turn(query) {
            if (!on()) return;
            try { console.groupEnd(); } catch (e) {}
            console.group('%c[html-notes] ▶ ' + JSON.stringify(query), S);
        },
        route(data) {
            if (!on()) return;
            if (data.path === 'agent') {
                // `status` says WHY the agent got this turn (deferred / defer /
                // none / skipped-removal), `classified` + `queries` say what the
                // tier-2 classifier thought the ask was, and `hint` says whether
                // the agent was actually told. Before this the line carried none
                // of it, so a misroute could only be diagnosed from server logs.
                const r = data.router || {};
                console.log('%c[html-notes] 🧭 route: AGENT (' + (r.status || 'unknown') + ')',
                    'color:#f59e0b;font-weight:bold', {
                        classified: r.widgets, queries: r.queries, targets: r.targets,
                        checks: r.checks, reason: r.reason, hint: r.hint,
                        followup: data.followup_target, focus: data.focus_id,
                        query: data.query,
                    });
            } else if (data.path === 'router') {
                // Without this branch a tier-2 build fell through to the fast-path
                // line below and printed "fast-path → undefined" — spawn_router_stream
                // sends `widgets`, not `widget_type`. The one event that names a
                // tier-2 misroute was unreadable.
                console.log('%c[html-notes] 🧭 route: ROUTER → ' + (data.widgets || []).join(', '),
                    'color:#22c55e;font-weight:bold', {
                        queries: data.queries, targets: data.targets,
                        reason: data.reason, query: data.query,
                    });
            } else {
                console.log('%c[html-notes] 🧭 route: fast-path → ' + data.widget_type, 'color:#22c55e;font-weight:bold', data);
            }
        },
        log(kind, ...rest) { if (on()) console.log(`[html-notes] ${kind}:`, ...rest); },
        component(html) {
            if (!on()) return;
            const types = widgetTypes(html);
            console.log('%c[html-notes] 🎨 rendered widgets:', S, types.length ? types : '(none)', { chars: (html || '').length });
            console.debug('[html-notes] component html ↓', html);
        },
        error(msg) { console.error('[html-notes] ❌', msg); },
        groupEnd() { if (on()) { try { console.groupEnd(); } catch (e) {} } },
    };
})();

// ─── LEGO: WIDGET MANAGER ─────────────────────────────────────────
window.WidgetManager = {
    getDismissed() {
        try {
            return JSON.parse(localStorage.getItem('dismissed_widgets') || '[]');
        } catch {
            return [];
        }
    },
    dismiss(widgetElement) {
        if (!widgetElement) return;
        const id = widgetElement.id;
        if (id) {
            const dismissed = this.getDismissed();
            if (!dismissed.includes(id)) {
                dismissed.push(id);
                localStorage.setItem('dismissed_widgets', JSON.stringify(dismissed));
            }
        }
        // Collapse to a scanline and blink out, like a CRT losing power, then
        // detach. animationend can be missed if the element is re-rendered
        // mid-animation, so a timeout guarantees the node still goes away.
        if (widgetElement.classList.contains('crt-off')) return;
        widgetElement.classList.remove('crt-on');
        widgetElement.classList.add('crt-off');
        const detach = () => widgetElement.remove();
        widgetElement.addEventListener('animationend', detach, { once: true });
        setTimeout(detach, 700);
    },
    isDismissed(id) {
        if (!id) return false;
        return this.getDismissed().includes(id);
    }
};

// ─── LEGO: WIDGET RESIZER ─────────────────────────────────────────
// Drag the bottom-right corner of any widget. Width snaps to grid columns
// (the grid is the layout, so a free width would just leave a ragged hole);
// height is free. Sizes are keyed by widget id and survive reload.
//
// reconcileCanvas() replaces a widget's DOM node whenever its content changes,
// which throws away any inline style we set — so the size lives in localStorage
// and is re-applied to each node as it appears, rather than being held on the
// element. A MutationObserver is what re-applies it, because widgets also arrive
// via history restore and first paint, not only through reconcileCanvas.
window.WidgetResizer = {
    MIN_COLS: 1,
    MAX_COLS: 4,
    MIN_HEIGHT: 180,
    MAX_HEIGHT: 1400,

    getSizes() {
        try {
            return JSON.parse(localStorage.getItem('widget_sizes') || '{}');
        } catch {
            return {};
        }
    },
    saveSize(id, size) {
        if (!id) return;
        const sizes = this.getSizes();
        if (size) sizes[id] = size; else delete sizes[id];
        localStorage.setItem('widget_sizes', JSON.stringify(sizes));
    },

    /** Grid geometry, so a pixel drag can be converted into a column count. */
    metrics(grid) {
        const style = getComputedStyle(grid);
        const tracks = style.gridTemplateColumns.split(' ').filter(Boolean);
        const colWidth = parseFloat(tracks[0]) || 340;
        const gap = parseFloat(style.columnGap) || 0;
        return { colWidth, gap, trackCount: Math.max(tracks.length, 1) };
    },

    apply(el) {
        const size = this.getSizes()[el.id];
        if (!size) return;
        // Inline styles beat the Tailwind col-span-*/h-[380px] classes baked into
        // the widget HTML by the server factory.
        if (size.cols) el.style.gridColumn = `span ${size.cols}`;
        if (size.height) el.style.height = `${size.height}px`;
    },

    decorate(el) {
        if (!el.id || el.querySelector(':scope > .widget-resize-handle')) return;
        const handle = document.createElement('div');
        handle.className = 'widget-resize-handle';
        handle.title = 'Drag to resize · double-click to reset';
        handle.addEventListener('pointerdown', e => this.startDrag(e, el, handle));
        handle.addEventListener('dblclick', e => {
            e.preventDefault();
            this.saveSize(el.id, null);
            el.style.removeProperty('grid-column');
            el.style.removeProperty('height');
            window.dispatchEvent(new Event('resize'));
        });
        el.appendChild(handle);
    },

    startDrag(e, el, handle) {
        e.preventDefault();
        e.stopPropagation();
        const grid = el.closest('.dashboard-grid');
        if (!grid) return;

        const { colWidth, gap, trackCount } = this.metrics(grid);
        const rect = el.getBoundingClientRect();
        const startX = e.clientX;
        const startY = e.clientY;
        const startW = rect.width;
        const startH = rect.height;
        const maxCols = Math.min(this.MAX_COLS, trackCount);

        el.classList.add('is-resizing');
        handle.setPointerCapture(e.pointerId);

        let cols = null;
        let height = null;

        const onMove = ev => {
            const width = startW + (ev.clientX - startX);
            // Round to the nearest whole number of column tracks (+ their gaps).
            cols = Math.round((width + gap) / (colWidth + gap));
            cols = Math.min(Math.max(cols, this.MIN_COLS), maxCols);

            height = startH + (ev.clientY - startY);
            height = Math.min(Math.max(Math.round(height), this.MIN_HEIGHT), this.MAX_HEIGHT);

            el.style.gridColumn = `span ${cols}`;
            el.style.height = `${height}px`;
        };

        const onUp = () => {
            handle.removeEventListener('pointermove', onMove);
            handle.removeEventListener('pointerup', onUp);
            handle.removeEventListener('pointercancel', onUp);
            el.classList.remove('is-resizing');
            if (cols !== null && height !== null) this.saveSize(el.id, { cols, height });
            // Chart.js sizes to its container on window resize; without this a
            // resized chart keeps its old canvas dimensions until the next paint.
            window.dispatchEvent(new Event('resize'));
        };

        handle.addEventListener('pointermove', onMove);
        handle.addEventListener('pointerup', onUp);
        handle.addEventListener('pointercancel', onUp);
    },

    scan(root) {
        (root || document).querySelectorAll('.widget-container').forEach(el => {
            this.apply(el);
            this.decorate(el);
            window.WidgetLayout.decorate(el);
        });
    },

    /** Re-apply sizes to widgets as they are (re)painted, however they arrive. */
    observe(canvas) {
        if (!canvas || this._observing) return;
        this._observing = true;
        this.scan(canvas);
        new MutationObserver(() => this.scan(canvas))
            .observe(canvas, { childList: true, subtree: true });
    }
};

// ─── LEGO: WIDGET LAYOUT (order persistence + drag-to-move) ────────
// The grid packs widgets by DOM order (grid-auto-flow: dense), so "where a widget
// sits" IS its position among its siblings. Two jobs:
//   1. Persist that order in localStorage so a refresh restores the arrangement
//      the user last saw — before this, history-restore repacked from scratch and
//      widgets appeared to jump around / stack differently.
//   2. Let the user drag a widget by its grip to reorder it, then remember it.
// This is reorder-drag, not free absolute positioning — a free x/y would just
// leave ragged holes in the grid, the same reason the resizer snaps width to
// columns. apply() is only ever called explicitly (load / reconcile), never from
// a MutationObserver, so reordering the DOM can't feed back into itself.
window.WidgetLayout = {
    KEY: 'widget_order',

    getOrder() {
        try { return JSON.parse(localStorage.getItem(this.KEY) || '[]'); }
        catch { return []; }
    },
    saveOrder(ids) {
        localStorage.setItem(this.KEY, JSON.stringify(ids));
    },
    /** Snapshot the grid's current child order as the saved arrangement. */
    capture(grid) {
        if (!grid) return;
        const ids = Array.from(grid.querySelectorAll(':scope > .widget-container'))
            .map(w => w.id).filter(Boolean);
        // Preserve the rank of any saved ids not currently on the canvas (a widget
        // dismissed this session shouldn't lose its slot for the next), by keeping
        // them after the live ones in their previous relative order.
        const live = new Set(ids);
        const carried = this.getOrder().filter(id => !live.has(id));
        this.saveOrder([...ids, ...carried]);
    },
    /** Reorder the grid's children to match the saved arrangement. Unknown/new
     *  widgets keep their DOM order and land after the known ones. */
    apply(grid) {
        if (!grid) return;
        const order = this.getOrder();
        if (!order.length) return;
        const rank = new Map(order.map((id, i) => [id, i]));
        const kids = Array.from(grid.querySelectorAll(':scope > .widget-container'));
        kids
            .map((el, i) => ({ el, i, r: rank.has(el.id) ? rank.get(el.id) : Infinity }))
            .sort((a, b) => (a.r - b.r) || (a.i - b.i)) // stable: DOM order breaks ties
            .forEach(({ el }) => grid.appendChild(el));  // appendChild MOVES the node
    },

    decorate(el) {
        if (!el.id || el.__moveWired) return;
        // Drag by the header bar (the standard title-bar-drag affordance) so the
        // handle never covers the widget's own content. Widgets with no header get
        // a small corner grip instead.
        let surface = el.querySelector(':scope > .widget-header');
        if (!surface) {
            surface = document.createElement('div');
            surface.className = 'widget-move-handle';
            surface.title = 'Drag to move';
            surface.innerHTML = '<span class="material-symbols-outlined">drag_indicator</span>';
            el.appendChild(surface);
        } else {
            surface.classList.add('widget-drag-surface');
            surface.title = 'Drag to move';
        }
        el.__moveWired = true;
        surface.addEventListener('pointerdown', e => this.startMove(e, el, surface));
    },

    startMove(e, el, surface) {
        // A click on the close button (or anything interactive in the header) must
        // not start a drag.
        if (e.target.closest && e.target.closest('button, a, input, select, textarea')) return;
        if (e.button !== undefined && e.button !== 0) return;
        e.preventDefault();
        const grid = el.closest('.dashboard-grid');
        if (!grid) return;
        el.classList.add('is-moving');
        surface.setPointerCapture(e.pointerId);
        const handle = surface;
        let raf = null;
        let moved = false;

        const reorder = ev => {
            raf = null;
            // The widget under the cursor, ignoring the one being dragged.
            const under = document.elementsFromPoint(ev.clientX, ev.clientY)
                .map(n => (n.closest ? n.closest('.widget-container') : null))
                .find(w => w && w !== el && w.parentElement === grid);
            if (!under) return;
            const rect = under.getBoundingClientRect();
            const before = ev.clientY < rect.top + rect.height / 2;
            if (before) grid.insertBefore(el, under);
            else grid.insertBefore(el, under.nextSibling);
            moved = true;
            if (window.__masonryLayout) window.__masonryLayout();
        };

        const onMove = ev => {
            if (raf) return;
            raf = requestAnimationFrame(() => reorder(ev));
        };
        const onUp = () => {
            handle.removeEventListener('pointermove', onMove);
            handle.removeEventListener('pointerup', onUp);
            handle.removeEventListener('pointercancel', onUp);
            el.classList.remove('is-moving');
            if (moved) {
                this.capture(grid);
                window.dispatchEvent(new Event('resize'));
            }
        };
        handle.addEventListener('pointermove', onMove);
        handle.addEventListener('pointerup', onUp);
        handle.addEventListener('pointercancel', onUp);
    }
};

document.addEventListener("DOMContentLoaded", () => {
    // Configure DOMPurify globally to allow Alpine.js attributes and event listeners
    if (window.DOMPurify) {
        DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
            const name = data.attrName;
            if (name.startsWith('x-') || name.startsWith('@') || name.startsWith(':')) {
                data.forceKeepAttr = true;
            }
        });
    }

    const state = {
        sessionId: localStorage.getItem("html_notes_session_id") || generateUUID(),
        mediaRecorder: null,
        audioChunks: [],
        isRecording: false,
        isMuted: localStorage.getItem("html_notes_is_muted") === "true",
        wakeWordActive: false,
        abortController: null,
        // The widget the next question is most likely ABOUT: the last one the
        // user touched. The server otherwise has to infer the follow-up target
        // from message text alone, which mis-targets whenever the newest widget
        // isn't the one being asked about. Sent as focus_widget_id.
        focusWidgetId: null
    };

    localStorage.setItem("html_notes_session_id", state.sessionId);

    // ─── THEME ENGINE ───────────────────────────────────────────────────────
    // Themes are pure CSS: <html data-theme="…"> selects a palette in
    // hud-theme.css. This engine sets the attribute, persists it, and keeps
    // Chart.js in sync (charts draw on <canvas>, so they can't read CSS vars on
    // their own — we feed them the current palette's ink/line colors and redraw
    // the live ones). An inline script in <head> applies the saved theme before
    // first paint to avoid a flash; this re-applies it to also sync the charts.
    const THEME_KEY = "html_notes_theme";
    window.HN = window.HN || {};
    window.__hnCharts = window.__hnCharts || new Set();

    window.HN.chartColors = function () {
        const cs = getComputedStyle(document.documentElement);
        const v = (n, d) => (cs.getPropertyValue(n).trim() || d);
        return {
            tick: v("--hud-ink-dim", "#8fb2cc"),
            grid: `rgba(${v("--hud-line-rgb", "120,210,255")}, 0.10)`,
            ink: v("--hud-ink", "#d7ecfb"),
        };
    };
    window.HN.registerChart = function (chart) {
        if (!chart) return;
        window.__hnCharts.add(chart);
        window.HN.themeChart(chart);
    };
    window.HN.themeChart = function (chart) {
        try {
            const c = window.HN.chartColors();
            const o = chart.options || (chart.options = {});
            o.color = c.ink;
            const leg = o.plugins && o.plugins.legend && o.plugins.legend.labels;
            if (leg) leg.color = c.ink;
            if (o.scales) for (const ax of Object.values(o.scales)) {
                if (ax && ax.ticks) ax.ticks.color = c.tick;
                if (ax && ax.grid) ax.grid.color = c.grid;
            }
        } catch (e) { /* a chart with an unusual options shape just keeps its baked colors */ }
    };
    window.HN.currentTheme = function () {
        return document.documentElement.getAttribute("data-theme") || "hud";
    };
    window.HN.applyTheme = function (name) {
        const root = document.documentElement;
        if (!name || name === "hud") root.removeAttribute("data-theme");
        else root.setAttribute("data-theme", name);
        try { localStorage.setItem(THEME_KEY, name || "hud"); } catch (e) {}
        if (window.Chart) {
            const c = window.HN.chartColors();
            Chart.defaults.color = c.ink;
            Chart.defaults.borderColor = c.grid;
        }
        // Recolor + redraw every live chart; drop any that were destroyed.
        window.__hnCharts.forEach(ch => {
            try { window.HN.themeChart(ch); ch.update("none"); }
            catch (e) { window.__hnCharts.delete(ch); }
        });
        // Interactive widgets (stock card) listen to redraw their own canvas.
        window.dispatchEvent(new CustomEvent("hn:theme", { detail: { name: name || "hud" } }));
    };
    // Sync Chart.defaults + charts to the theme the head script already applied.
    window.HN.applyTheme(localStorage.getItem(THEME_KEY) || "hud");

    window.WidgetResizer.observe(document.getElementById("live-canvas"));

    // ─── APP RAIL ───────────────────────────────────────────────────────────
    // Collapsible launcher of the user's own apps so nobody has to REMEMBER
    // what containers exist: same curated /api/services list the App Hub grid
    // polls (portal inventory ⊕ registry ⊕ DB overlay). Pinned first, then
    // client frontends, then '-service' backends; hidden apps stay hidden.
    (function initAppRail() {
        const rail = document.getElementById("app-rail");
        if (!rail) return;
        document.body.classList.add("has-rail");
        const KEY = "html_notes_app_rail_open";
        let open = false;
        try { open = localStorage.getItem(KEY) === "1"; } catch (e) {}
        let apps = [];
        const escAttr = (s) => String(s ?? "").replace(/[&<>"']/g,
            (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
        const dotColor = (s) => s === "healthy" ? "#34d399"
            : (s === "unknown" || s == null) ? "#8fb2cc" : "#f87171";

        function render() {
            rail.classList.toggle("expanded", open);
            const rows = apps.map((a) => `
                <a class="rail-item" href="${escAttr(a.launch_url)}" target="_blank"
                   rel="noopener" title="${escAttr(a.name)} — ${escAttr(a.status || "unknown")}">
                    <span class="rail-icon">${escAttr(a.icon || "🧩")}<span class="rail-dot"
                        style="background:${dotColor(a.status)}"></span></span>
                    <span class="rail-name">${escAttr(a.name)}</span>
                </a>`);
            // Separator between frontends and backends when both are present.
            const firstBackend = apps.findIndex((a) => /-service$/.test(a.id || ""));
            if (firstBackend > 0) rows.splice(firstBackend, 0, '<hr class="rail-sep">');
            rail.innerHTML = `
                <button class="rail-toggle" data-rail-toggle
                        title="${open ? "Collapse" : "Your apps"}">
                    <span class="rail-chevron">⟩</span><span class="rail-name">Your apps</span>
                </button>
                <nav class="rail-list">${rows.join("")}</nav>`;
        }

        async function refresh() {
            try {
                const r = await fetch("/api/services");
                const d = await r.json();
                const all = ((d && d.apps) || []).filter((a) => a && !a.hidden && a.launch_url);
                const rank = (a) => (a.pinned ? 0 : /-service$/.test(a.id || "") ? 2 : 1);
                all.sort((x, y) => rank(x) - rank(y) || String(x.name).localeCompare(String(y.name)));
                apps = all;
            } catch (e) { /* keep the last good list — portal blips must not blank the rail */ }
            render();
        }

        rail.addEventListener("click", (e) => {
            if (!e.target.closest("[data-rail-toggle]")) return;
            open = !open;
            try { localStorage.setItem(KEY, open ? "1" : "0"); } catch (err) {}
            render();
        });

        render();
        refresh();
        setInterval(refresh, 60000);
    })();

    // ─── FOLLOW-UP FOCUS TRACKING ───────────────────────────────────────────
    // Which widget did the question come from? Delegated on the canvas rather
    // than bound per widget, so it survives reconcileCanvas replacing nodes.
    // Capture phase: a click on a widget's own control (close, a link) still
    // registers the focus before that control's handler runs.
    (function trackWidgetFocus() {
        const canvas = document.getElementById("live-canvas");
        if (!canvas) return;
        const remember = (e) => {
            const w = e.target.closest?.(".widget-container, .glass-card");
            if (!w || !w.id) return;
            state.focusWidgetId = w.id;
            canvas.querySelectorAll(".is-focused").forEach(n => {
                if (n !== w) n.classList.remove("is-focused");
            });
            w.classList.add("is-focused");
        };
        canvas.addEventListener("pointerdown", remember, true);
        canvas.addEventListener("focusin", remember, true);
    })();

    // Broken-image gate. Widget images come from third-party og:image / thumbnail
    // URLs that frequently hotlink-block or 404, and an <img> that fails shows a
    // broken-frame icon. Inline onerror handlers get stripped by the canvas
    // DOMPurify pass, so instead we catch the (non-bubbling) `error` event in the
    // CAPTURE phase on the canvas — one listener covers every current and future
    // image widget. A failed image is hidden and its tile marked so CSS can show a
    // clean placeholder instead of a broken frame.
    (function installImageErrorGate() {
        const canvas = document.getElementById("live-canvas");
        if (!canvas) return;
        canvas.addEventListener("error", (e) => {
            const img = e.target;
            if (!img || img.tagName !== "IMG") return;
            if (img.dataset.imgFailed) return; // guard against loops

            // RETRY ONCE with the source's favicon before giving up. og:image URLs
            // hotlink-block far more often than favicons do, so a dead article
            // photo usually still has a usable site mark — a recognisable source
            // tile beats a blank one. Guarded by data-imgRetried so a failing
            // favicon can't loop.
            const link = img.closest("a[href]") || img.parentElement?.querySelector?.("a[href]");
            const href = link?.getAttribute("href") || img.dataset.srcPage || "";
            if (!img.dataset.imgRetried && href && !img.src.includes("s2/favicons")) {
                try {
                    const host = new URL(href, location.href).hostname;
                    if (host) {
                        img.dataset.imgRetried = "1";
                        img.src = `https://www.google.com/s2/favicons?domain=${host}&sz=128`;
                        return;
                    }
                } catch (_) { /* not a usable URL — fall through to the placeholder */ }
            }

            img.dataset.imgFailed = "1";
            img.style.display = "none";
            // `.data-card-figure` is the data_card's article figure (the old
            // `.hero-image` band); without it a failed hero left an empty box
            // rather than a placeholder.
            const tile = img.closest(
                "figure, .data-card-figure, .product-media, .image-widget-body > *");
            if (tile) tile.classList.add("img-failed");
        }, true);
    })();

    const elements = {
        liveCanvas: document.getElementById("live-canvas"),
        chatInput: document.getElementById("chat-input"),
        btnSendMessage: document.getElementById("btn-send-message"),
        btnStopMessage: document.getElementById("btn-stop-message"),
        btnMic: document.getElementById("btn-mic"),
        recordingStatus: document.getElementById("recording-status"),
        healthIndicator: document.getElementById("health-indicator"),
        welcomeMessage: document.getElementById("welcome-message"),
        execLogContainer: document.getElementById("execution-log-container"),
        execLogContent: document.getElementById("execution-log-content"),
        btnToggleLog: document.getElementById("btn-toggle-log"),
        modelSelect: document.getElementById("model-select"),
        btnMute: document.getElementById("btn-mute"),
        btnForget: document.getElementById("btn-forget"),
        queueBadge: document.getElementById("queue-badge"),
        turnStatusContainer: document.getElementById("turn-status-container"),
        chatHistoryPanel: document.getElementById("chat-history-panel"),
        chatHistoryMessages: document.getElementById("chat-history-messages"),
        btnClearHistory: document.getElementById("btn-clear-history"),
        btnToggleHistory: document.getElementById("btn-toggle-history"),
        chatHistoryHeader: document.getElementById("chat-history-header")
    };

    if (elements.btnToggleLog) {
        elements.btnToggleLog.addEventListener("click", () => {
            if (elements.execLogContent.style.display === "none") {
                elements.execLogContent.style.display = "block";
                elements.btnToggleLog.innerText = "▼";
            } else {
                elements.execLogContent.style.display = "none";
                elements.btnToggleLog.innerText = "▶";
            }
        });
    }

    // ─── WAKE WORD ─────────────────────────────────────────
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
        const recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.lang = 'en-US';

        recognition.onresult = (event) => {
            if (state.isMuted || state.isRecording) return;
            const transcript = event.results[event.results.length - 1][0].transcript.trim().toLowerCase();
            console.log("[WakeWord] Heard:", transcript);
            
            if (transcript.includes("hey canvas")) {
                const command = transcript.split("hey canvas")[1].trim();
                if (command.length > 0) {
                    elements.chatInput.value = command;
                    sendChatMessage();
                } else {
                    elements.chatInput.placeholder = "Listening...";
                    state.wakeWordActive = true;
                }
            } else if (state.wakeWordActive) {
                elements.chatInput.value = transcript;
                sendChatMessage();
                elements.chatInput.placeholder = "Type a command to update the canvas...";
                state.wakeWordActive = false;
            }
        };

        recognition.onend = () => {
            if (!state.isRecording) {
                try { recognition.start(); } catch (e) {}
            }
        };
        
        try { recognition.start(); } catch (e) {}
    } else {
        console.warn("SpeechRecognition not supported in this browser.");
    }

    // Auto-resize textarea
    elements.chatInput.addEventListener("input", function() {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';
    });

    // ─── EVENT HANDLERS ───────────────────────────────────────
    elements.btnSendMessage.addEventListener("click", sendChatMessage);
    elements.chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    // Stop cancels EVERY running turn and drops anything still queued — otherwise
    // the queue would just start the next message the user was trying to stop,
    // and sibling turns would keep streaming.
    function stopEverything() {
        chatQueue.length = 0;
        for (const controller of activeTurns) {
            controller.abort();
        }
        updateQueueIndicator();
        clearSpeechQueue();
    }

    if (elements.btnStopMessage) {
        elements.btnStopMessage.addEventListener("click", stopEverything);
    }

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && (activeTurns.size || chatQueue.length)) {
            stopEverything();
        }
    });

    elements.btnMic.addEventListener("click", toggleRecording);

    // Initial check
    checkHealth();
    fetchModels();
    setInterval(checkHealth, 30000);
    updateMuteButtonUI();

    // Prime audio on the first real user interaction so TTS fired off the SSE
    // stream (or the wake word) isn't rejected by the browser autoplay policy.
    ["pointerdown", "keydown", "touchstart"].forEach(evt => {
        window.addEventListener(evt, primeAudioPlayback, { once: true, passive: true });
    });

    // Load history
    loadHistory();

    // Mute control, shared by the command-bar button AND the settings widget so
    // both stay in sync. Exposed on window.HN for the settings panel.
    window.HN.setMuted = function (next) {
        state.isMuted = !!next;
        localStorage.setItem("html_notes_is_muted", state.isMuted);
        updateMuteButtonUI();
        if (state.isMuted) {
            clearSpeechQueue();
        } else {
            // Unmuting is a user gesture: clear any offline latch and prime
            // audio so playback isn't blocked by autoplay policy.
            markTtsHealthy();
            primeAudioPlayback();
        }
        window.dispatchEvent(new CustomEvent("hn:mute", { detail: { muted: state.isMuted } }));
    };
    window.HN.isMuted = function () { return !!state.isMuted; };

    // Mute Button listener
    if (elements.btnMute) {
        elements.btnMute.addEventListener("click", () => window.HN.setMuted(!state.isMuted));
    }

    // Forget-me listener — wipe the persistent user profile (name/location/likes).
    if (elements.btnForget) {
        elements.btnForget.addEventListener("click", async () => {
            if (!confirm("Forget everything the assistant remembers about you (name, location, preferences)?")) return;
            try {
                const res = await fetch("/user/memory", { method: "DELETE" });
                const data = await res.json().catch(() => ({}));
                appendChatMessageToHistory("assistant",
                    data && data.forgotten ? `Done — forgot ${data.forgotten} thing${data.forgotten === 1 ? "" : "s"} about you.`
                                           : "There was nothing remembered about you.");
            } catch (e) {
                console.error("Forget-me failed:", e);
                appendChatMessageToHistory("assistant", "Couldn't clear your memory right now.");
            }
        });
    }

    // Toggle history listener
    if (elements.chatHistoryHeader && elements.chatHistoryMessages && elements.btnToggleHistory) {
        elements.chatHistoryHeader.addEventListener("click", (e) => {
            // Prevent toggle if clicking the clear button
            if (e.target.closest('#btn-clear-history')) return;
            
            const isHidden = elements.chatHistoryMessages.style.display === "none";
            if (isHidden) {
                elements.chatHistoryMessages.style.display = "flex";
                elements.btnToggleHistory.innerText = "▼";
            } else {
                elements.chatHistoryMessages.style.display = "none";
                elements.btnToggleHistory.innerText = "▲";
            }
        });
    }

    // Clear history listener
    if (elements.btnClearHistory) {
        elements.btnClearHistory.addEventListener("click", () => {
            if (confirm("Are you sure you want to clear chat history and start a new canvas?")) {
                state.sessionId = generateUUID();
                localStorage.setItem("html_notes_session_id", state.sessionId);
                elements.chatHistoryMessages.innerHTML = "";
                elements.liveCanvas.innerHTML = `
                    <div id="dashboard-grid" class="dashboard-grid">
                        <div id="welcome-message" class="system-message col-span-full">
                            <h1>Canvas Ready</h1>
                            <p>Tell the LLM what to build. It will be added as a widget to the dashboard.</p>
                        </div>
                    </div>
                `;
                clearSpeechQueue();
            }
        });
    }

    // ─── RECORDING LOGIC ───────────────────────────────────────
    async function toggleRecording() {
        if (state.isRecording) {
            stopRecording();
        } else {
            await startRecording();
        }
    }

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            state.mediaRecorder = new MediaRecorder(stream);
            state.audioChunks = [];

            state.mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) {
                    state.audioChunks.push(e.data);
                }
            };

            state.mediaRecorder.onstop = async () => {
                elements.recordingStatus.innerText = "Transcribing...";
                const audioBlob = new Blob(state.audioChunks, { type: 'audio/webm' });
                
                // Convert blob to base64
                const reader = new FileReader();
                reader.readAsDataURL(audioBlob);
                reader.onloadend = async () => {
                    const base64Audio = reader.result.split(',')[1];
                    try {
                        const res = await fetch("/session/transcribe", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ audio: base64Audio })
                        });
                        if (res.ok) {
                            const data = await res.json();
                            const transcript = data.text || "";
                            if (transcript) {
                                elements.chatInput.value = transcript;
                                // Auto-trigger send if desired, or let user review. We'll let user review or just send it immediately?
                                // Let's just put it in the input so they can edit.
                                elements.chatInput.style.height = 'auto';
                                elements.chatInput.style.height = (elements.chatInput.scrollHeight) + 'px';
                            }
                        } else {
                            console.error("Transcription failed", await res.text());
                            alert("Transcription failed. Please try again.");
                        }
                    } catch (err) {
                        console.error("Transcription error:", err);
                    } finally {
                        resetRecordingUI();
                    }
                };
            };

            state.mediaRecorder.start();
            state.isRecording = true;
            elements.btnMic.classList.add("recording");
            elements.recordingStatus.style.display = "flex";
            elements.recordingStatus.innerHTML = '<span class="pulse"></span> Recording...';
        } catch (err) {
            console.error("Microphone access denied or error:", err);
            alert("Could not access microphone.");
        }
    }

    function stopRecording() {
        if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
            state.mediaRecorder.stop();
            state.mediaRecorder.stream.getTracks().forEach(t => t.stop());
        }
        state.isRecording = false;
        elements.btnMic.classList.remove("recording");
    }

    function resetRecordingUI() {
        elements.recordingStatus.style.display = "none";
    }

    let lastRenderedComponentHtml = null;

    // Every `component` event carries the FULL canvas, not a diff. Assigning it
    // straight to innerHTML tore down and recreated every widget's DOM node on
    // every single mutation — including ones nobody touched — which reset their
    // Alpine state and forced YouTube iframes to reload and the music player to
    // re-init, causing a visible stutter each time any widget was added. This
    // snapshot lets reconcileCanvas() recognize an untouched widget and leave
    // its live DOM node — and its in-flight iframe/audio — completely alone.
    const widgetSourceSnapshots = new Map();

    // The client's canvas becomes the server's canonical copy for the next
    // turn whenever no other turn is in flight (see the backend's
    // "adopt client canvas" comment) — and what the client has by then is
    // ALPINE-RENDERED markup: x-text spans have real text in them, x-show
    // elements carry an inline display:none/"" Alpine set at runtime, and a
    // bound :href/:src has a concrete resolved attribute alongside it. None
    // of that exists in the server's own templates, so comparing it against
    // a pristine (or differently-aged Alpine) render of the same widget
    // always looks "changed" even when the actual config is identical.
    // Stripping those volatile, Alpine-owned bits from BOTH sides before
    // comparing makes the check fair regardless of which side happens to be
    // pristine vs. already-rendered.
    function normalizeForComparison(node) {
        const clone = node.cloneNode(true);
        [clone, ...clone.querySelectorAll('*')].forEach(el => {
            if (el.hasAttribute('x-text')) el.textContent = '';
            if (el.hasAttribute('x-show')) el.removeAttribute('style');
            Array.from(el.attributes).forEach(attr => {
                if (attr.name.startsWith(':')) {
                    const real = attr.name.slice(1);
                    if (real && real !== 'class' && real !== 'style') el.removeAttribute(real);
                }
            });
        });

        // Whitespace-only text nodes are pure formatting. Whether they survive
        // depends on which pipeline last serialized the markup — a fresh Python
        // f-string vs. round-tripped through a live browser DOM via innerHTML —
        // not on the widget's actual config, so they're noise for this check.
        const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
        const blank = [];
        let n;
        while ((n = walker.nextNode())) {
            if (!n.textContent.trim()) blank.push(n);
        }
        blank.forEach(n => n.remove());

        return clone;
    }

    // A freshly inserted/replaced widget is measured by the masonry script for its
    // grid-row span. If it holds an <img>/<iframe> that has not finished loading,
    // the span is computed against a too-short height and the next widget rides up
    // over it (the reported "widgets on top of each other"). The window 'load'
    // listener only fires ONCE at page load, so dynamically-added media never
    // triggers a relayout on its own. Re-run masonry when each media element
    // settles. (Already-cached media won't re-fire 'load', but those are measured
    // correctly on the first pass anyway.)
    function relayoutOnMediaSettle(node) {
        if (!node || !node.querySelectorAll) return;
        node.querySelectorAll('img, iframe').forEach(function (el) {
            if (el.__masonryLoadHooked) return;
            el.__masonryLoadHooked = true;
            var relayout = function () { if (window.__masonryLayout) window.__masonryLayout(); };
            el.addEventListener('load', relayout);
            el.addEventListener('error', relayout);
        });
    }

    /**
     * Give a just-reconciled widget a visible tell: 'is-entering' for a brand-new
     * card, 'is-updating' for one rewritten in place by a follow-up. Without this
     * an in-place rewrite was visually identical to nothing happening, which is
     * what "I had to refresh to see the widget change" actually looked like.
     *
     * The class is stripped on animationend so the SAME widget can flash again on
     * the next follow-up (a class left on would never retrigger). A timeout backs
     * that up in case animationend never fires (reduced-motion, tab in the
     * background), so a widget can't get stuck wearing the highlight.
     */
    /**
     * Glitch a widget's text from its OLD wording to its new one.
     *
     * A follow-up rewriting a card used to swap the text instantly, which read as
     * a silent substitution. Now the old words visibly decay into noise and the
     * new answer resolves out of it, left to right.
     *
     * Text NODES are rewritten in place — no wrapper elements are created. That
     * matters for more than performance: an earlier span-based version had to be
     * unwrapped again afterwards, because the server adopts the client canvas as
     * canonical and any leftover markup would be baked in permanently and would
     * change the widget's data-sig. With nothing added there is nothing to leak;
     * the only state is the text itself, and finishGlitches() restores it.
     *
     * Character COUNT is preserved on every frame, so the text keeps its shape
     * and the card never reflows around a growing string.
     */
    // Timing is tuned so the reveal READS as a live print — you should watch the
    // wavefront travel and see individual words resolve. The previous values
    // (0.45ms/char capped at 1500ms) put even a full research card under a second
    // and a half: the effect fired correctly but crossed the whole card faster
    // than the eye tracks it, so it registered as a flicker rather than as text
    // being rewritten. The cap is what bound it — a 2800-char card wanted 1260ms
    // and got clamped anyway, so raising ms/char alone would have changed nothing.
    const GLITCH_MIN_MS = 900;
    const GLITCH_MAX_MS = 4000;
    const GLITCH_MS_PER_CHAR = 1.6;
    // Fraction of the run a single character spends scrambling before it settles.
    // Small = a crisp left-to-right wipe; large = the whole card boils at once.
    // Kept low so the wavefront stays a legible edge sweeping across the card
    // rather than every word churning simultaneously.
    const GLITCH_CHAR_LIFE = 0.22;
    const GLITCH_MAX_CHARS = 9000;   // runaway guard, not a normal-size cutoff
    const GLITCH_GLYPHS = '!<>-_\\/[]{}=+*^?#%&@$~;:aAbB0123456789';
    // Live animations, so a request going out mid-glitch can force them to their
    // final text first — the canvas we send must never contain noise.
    const activeGlitches = new Set();

    function finishGlitches() {
        activeGlitches.forEach(g => g.finish());
        activeGlitches.clear();
    }

    /**
     * Run the glitch after the paint pipeline has settled.
     *
     * Two frames, then re-find the widget BY ID rather than holding the node
     * reference: whatever runs after reconcileCanvas's loop (WidgetLayout.apply,
     * renderDynamicComponents, Alpine init) can replace the node we were handed,
     * and animating a detached node is invisible work. Measured the hard way —
     * 488 words animated, zero of them on screen.
     */
    function scheduleGlitch(grid, widgetId, oldText) {
        if (!widgetId) return;
        requestAnimationFrame(() => requestAnimationFrame(() => {
            const live = grid.querySelector(`#${CSS.escape(widgetId)}`);
            if (live && live.isConnected) glitchIntoText(live, oldText);
        }));
    }

    // Widgets whose content is live, interactive, or continuously re-rendered.
    // Keyed on the authoritative data-widget-type stamp — the previous
    // '.youtube-widget' / '.music-widget' class selectors matched NOTHING (no
    // renderer emits those classes; those widgets were saved only by the
    // isAlpineDriven structural check below).
    const GLITCH_SKIP_SELECTOR = [
        '.map-widget',
        '[data-widget-type="map"]',
        '[data-widget-type="youtube_player"]',
        '[data-widget-type="mini_music_player"]'
    ].join(',');

    /**
     * True when Alpine actively manages this widget's contents.
     *
     * Enumerating component names was tried first and was wrong within minutes —
     * the music player is `musicPlayerWidget`, not the `miniMusicPlayer` its
     * widget_type suggested, so it silently fell through the skip list. Read the
     * structure instead: a STATIC card carries the trivial `x-data="{}"`, a live
     * one carries a component call. That holds for widgets nobody has written yet.
     */
    function isAlpineDriven(el) {
        const xd = (el.getAttribute && el.getAttribute('x-data') || '').trim();
        return !!xd && xd !== '{}' && xd !== '{ }';
    }

    /** The text nodes worth animating: prose only, never chrome or Alpine's. */
    function glitchTextNodes(widget) {
        const walker = document.createTreeWalker(widget, NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                if (!node.nodeValue || !node.nodeValue.trim())
                    return NodeFilter.FILTER_REJECT;
                for (let p = node.parentElement; p && p !== widget; p = p.parentElement) {
                    const tag = p.tagName;
                    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEMPLATE' ||
                        tag === 'BUTTON' || tag === 'svg')
                        return NodeFilter.FILTER_REJECT;
                    // Alpine owns this text and will rewrite it under us.
                    if (p.hasAttribute('x-text') || p.hasAttribute('x-html') ||
                        p.hasAttribute('x-for'))
                        return NodeFilter.FILTER_REJECT;
                    if (p.classList && p.classList.contains('widget-header'))
                        return NodeFilter.FILTER_REJECT;
                }
                return NodeFilter.FILTER_ACCEPT;
            }
        });
        const out = [];
        for (let n = walker.nextNode(); n; n = walker.nextNode()) out.push(n);
        return out;
    }

    /** Concatenated prose of a widget — the "before" text for the morph. */
    function captureWidgetText(widget) {
        try {
            return glitchTextNodes(widget).map(n => n.nodeValue).join('');
        } catch (e) {
            return '';
        }
    }

    function glitchIntoText(widget, oldText) {
        try {
            if (!widget || !widget.querySelectorAll) return;
            if (window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
            if (widget.matches(GLITCH_SKIP_SELECTOR)) return;
            if (isAlpineDriven(widget)) return;

            const nodes = glitchTextNodes(widget);
            if (!nodes.length) return;

            const finals = nodes.map(n => n.nodeValue);
            const totalChars = finals.reduce((a, s) => a + s.length, 0);
            if (!totalChars || totalChars > GLITCH_MAX_CHARS) return;

            const prev = oldText || '';
            const totalMs = Math.max(GLITCH_MIN_MS,
                Math.min(GLITCH_MAX_MS, totalChars * GLITCH_MS_PER_CHAR));
            const started = performance.now();

            const handle = {
                finish() {
                    activeGlitches.delete(handle);
                    nodes.forEach((n, i) => {
                        if (n.nodeValue !== finals[i]) n.nodeValue = finals[i];
                    });
                }
            };
            activeGlitches.add(handle);

            const tick = (now) => {
                // Replaced by a newer update, or removed — the new node runs its
                // own glitch; ours would just fight it.
                if (!widget.isConnected) { activeGlitches.delete(handle); return; }
                const t = (now - started) / totalMs;
                if (t >= 1) { handle.finish(); return; }

                let offset = 0;
                for (let ni = 0; ni < nodes.length; ni++) {
                    const final = finals[ni];
                    let out = '';
                    for (let i = 0; i < final.length; i++) {
                        const ch = final[i];
                        // Whitespace never scrambles: it holds the word shapes, so
                        // the card still reads as text rather than a block of noise.
                        if (ch === ' ' || ch === '\n' || ch === '\t') { out += ch; offset++; continue; }
                        const charStart = ((offset) / totalChars) * (1 - GLITCH_CHAR_LIFE);
                        const p = (t - charStart) / GLITCH_CHAR_LIFE;
                        if (p >= 1) out += ch;
                        // Not started yet: show the OLD character from this
                        // position. Old and new wording rarely line up, so in
                        // practice this reads as jumbled text rather than the
                        // previous answer — which is the intended effect (the
                        // card visibly scrambles), and it means the not-yet-
                        // animated region is never a stale readable answer.
                        else if (p <= 0) out += (prev[offset] && prev[offset].trim() ? prev[offset] : ch);
                        else out += GLITCH_GLYPHS[(Math.random() * GLITCH_GLYPHS.length) | 0];
                        offset++;
                    }
                    if (nodes[ni].nodeValue !== out) nodes[ni].nodeValue = out;
                }
                requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);

            // Never leave a card full of noise if rAF is starved (backgrounded
            // tab) or something throws mid-run.
            setTimeout(() => handle.finish(), totalMs + 1500);
        } catch (e) {
            console.warn('glitchIntoText failed', e);
            finishGlitches();
        }
    }

    // ── Paced widget reveal ───────────────────────────────────────────────
    // Widgets used to land the instant the canvas frame arrived, all at once,
    // while the narration describing them played independently — so a burst of
    // asks dumped the whole grid and then talked about it afterwards. A new
    // widget is now held back and revealed as its sentence STARTS being spoken,
    // so the canvas fills at the pace of the voice.
    //
    // SAFETY IS THE WHOLE DESIGN HERE: a widget must never be lost because audio
    // failed. Every hold has a hard timeout, anything queued is flushed the moment
    // speech stops, and gating is skipped entirely when TTS can't run (muted, or
    // the service is in its offline back-off — it was down for a whole session
    // recently, which would have hidden every widget on the canvas).
    const REVEAL_HOLD_MAX_MS = 3500;   // never hold a widget longer than this
    const pendingReveals = [];         // widget ids awaiting their cue, in order
    // Set from a chunk's `widget_id` and consumed by the next sentence
    // enqueued, so a sentence reveals the widget it is ABOUT. Falls back to
    // queue order when the server sends no id (the agent streams free prose
    // token-by-token, where no such pairing exists).
    let pendingSentenceWidgetId = null;

    function revealGateActive() {
        // Only gate when speech is actually going to happen for THIS turn.
        return ttsAvailable() && (isProcessingQueue || ttsQueue.length > 0);
    }

    function holdWidgetForReveal(el) {
        if (!el || !el.id) return false;
        el.classList.add('is-pending-reveal');
        pendingReveals.push(el.id);
        // Backstop: if no sentence ever cues this widget (TTS died mid-turn, the
        // model wrote fewer sentences than widgets), show it anyway.
        setTimeout(() => revealWidget(el.id), REVEAL_HOLD_MAX_MS);
        return true;
    }

    function revealWidget(id) {
        const idx = pendingReveals.indexOf(id);
        if (idx !== -1) pendingReveals.splice(idx, 1);
        const el = document.getElementById(id);
        if (!el || !el.classList.contains('is-pending-reveal')) return;
        el.classList.remove('is-pending-reveal');
        flagCanvasChange(el, 'is-entering');
        if (window.__masonryLayout) requestAnimationFrame(window.__masonryLayout);
    }

    // Called when a sentence begins playing: bring in the next widget in step.
    function revealNextWidget() {
        if (pendingReveals.length) revealWidget(pendingReveals[0]);
    }

    // Called when speech ends, on mute, and before serializing the canvas.
    function revealAllPending() {
        while (pendingReveals.length) revealWidget(pendingReveals[0]);
        // Belt and braces: catch any node whose id left the queue but kept the
        // class (e.g. replaced mid-flight), so nothing can stay invisible.
        document.querySelectorAll('.widget-container.is-pending-reveal')
            .forEach(el => {
                el.classList.remove('is-pending-reveal');
                flagCanvasChange(el, 'is-entering');
            });
    }

    function flagCanvasChange(el, cls) {
        if (!el || !el.classList) return;
        el.classList.remove('is-entering', 'is-updating');
        // Force a reflow so re-adding the class restarts the animation even when
        // the node is replaced with an identical class list.
        void el.offsetWidth;
        el.classList.add(cls);
        let done = false;
        const clear = () => {
            if (done) return;
            done = true;
            el.classList.remove(cls);
        };
        el.addEventListener('animationend', clear, { once: true });
        setTimeout(clear, 1200);
    }

    function reconcileCanvas(container, rawHtml) {
        const clean = DOMPurify.sanitize(rawHtml, CANVAS_DOMPURIFY_CONFIG);
        const doc = new DOMParser().parseFromString(clean, 'text/html');

        let grid = container.querySelector('#dashboard-grid');
        if (!grid) {
            container.innerHTML = '<div id="dashboard-grid" class="dashboard-grid"></div>';
            grid = container.querySelector('#dashboard-grid');
        }

        // The very first widget in a session can arrive without a #dashboard-grid
        // wrapper (nothing existed yet for the server to append into) — fall back
        // to any widget-container found anywhere in the parsed document.
        const sourceRoot = doc.querySelector('#dashboard-grid') || doc.body;
        // Match loadHistory's widget census: create_widget custom widgets are
        // '.glass-card canvas-widget', NOT '.widget-container' — selecting only
        // the latter meant a custom widget's component event never painted live
        // (the tool reported success, nothing appeared until reload), and a
        // server-side removal of one left a zombie card on screen forever.
        const newWidgets = Array.from(sourceRoot.querySelectorAll('.widget-container, .glass-card, .canvas-widget'))
            .filter(w => !w.parentElement || !w.parentElement.closest('.widget-container, .glass-card, .canvas-widget'));
        const newIds = new Set(newWidgets.map(w => w.id).filter(Boolean));

        // The server dropped a widget (explicit remove) — take it off the live canvas.
        Array.from(grid.querySelectorAll('.widget-container, .glass-card, .canvas-widget'))
            .filter(w => !w.parentElement || !w.parentElement.closest('.widget-container, .glass-card, .canvas-widget'))
            .forEach(existing => {
                // Turn envelopes are CLIENT-ONLY, so every id they could carry is
                // "not in the server's set" by construction and this loop would
                // delete the card that is narrating the very turn painting here.
                // They deliberately carry NO widget class (see the masonry note in
                // index.html), so today they never reach this selector — this stays
                // as a guard because the failure is silent and a later styling
                // tweak that adds .glass-card would resurrect it.
                if (existing.hasAttribute('data-turn-envelope')) return;
                if (existing.id && !newIds.has(existing.id)) {
                    widgetSourceSnapshots.delete(existing.id);
                    existing.remove();
                }
            });

        let changed = false;
        newWidgets.forEach(newWidget => {
            const id = newWidget.id;

            // No id to key on — safest to treat as new content every time.
            if (!id) {
                grid.appendChild(newWidget);
                relayoutOnMediaSettle(newWidget);
                changed = true;
                return;
            }

            const existing = grid.querySelector(`#${CSS.escape(id)}`);
            const prevSnapshot = widgetSourceSnapshots.get(id);
            // Record what the server just sent as this widget's latest source,
            // for the next reconcile's fallback comparison.
            widgetSourceSnapshots.set(id, newWidget.cloneNode(true));

            if (existing) {
                // Primary check: the server-stamped content signature. It is
                // computed from the widget's DATA, so it is immune to Alpine's
                // post-init DOM rewrites (an inserted <iframe> sibling, merged
                // :class values) that make a structural HTML compare wrongly
                // report an untouched widget as "changed" and stutter-replace
                // its live node. Read the sig off the LIVE node — Alpine never
                // touches data-* attributes, so it is always the one this
                // widget was last rendered with.
                const newSig = newWidget.getAttribute('data-sig');
                const liveSig = existing.getAttribute('data-sig');
                if (newSig && liveSig) {
                    if (newSig === liveSig) return; // unchanged — keep the live node (and its media)
                } else if (prevSnapshot &&
                           normalizeForComparison(prevSnapshot).isEqualNode(normalizeForComparison(newWidget))) {
                    // One side is unsigned (a custom create_widget or a canvas
                    // saved before signatures existed) — fall back to the
                    // structural comparison against the previous paint.
                    return;
                }
                // Capture the outgoing wording BEFORE the swap — the glitch morphs
                // old text into new, so frame one must still look like the answer
                // the user was reading.
                const outgoingText = captureWidgetText(existing);
                existing.replaceWith(newWidget);
                flagCanvasChange(newWidget, 'is-updating');
                // A follow-up rewrote this card in place. Print the new wording
                // in rather than swapping it instantly, so it reads as "this
                // answer is being revised" instead of a silent substitution.
                //
                // Deferred by two frames, and re-queried by id, because running it
                // synchronously here does not work: the node we just wrapped is
                // discarded before the first animation frame by the work that runs
                // after this loop (WidgetLayout.apply, renderDynamicComponents,
                // Alpine init). Measured — 488 words wrapped, zero ever revealed,
                // spans gone within 150ms. Animate whatever node is actually live
                // once the paint pipeline has settled.
                scheduleGlitch(grid, newWidget.id, outgoingText);
            } else {
                grid.appendChild(newWidget);
                // Hold it back so it appears in time with the narration; shows
                // immediately when nothing is going to be spoken. EXCEPT a
                // provisional widget — its entire purpose is to be visible
                // while the agent is still composing, so it never waits for
                // narration.
                if (newWidget.hasAttribute('data-provisional')
                    || !(revealGateActive() && holdWidgetForReveal(newWidget))) {
                    flagCanvasChange(newWidget, 'is-entering');
                }
            }
            relayoutOnMediaSettle(newWidget);
            changed = true;
        });

        // Restore the user's saved arrangement (and place any brand-new widget at
        // the end) so the grid packs the same way every paint.
        window.WidgetLayout.apply(grid);

        grid.style.removeProperty('min-height');
        // Drive the masonry recompute ourselves instead of waiting on the
        // MutationObserver's timing — a replaced node lost its observer
        // registration, and a too-short span left standing for even one frame is a
        // visible overlap. The observer/second-pass still run as backups.
        if (changed && window.__masonryLayout) {
            requestAnimationFrame(window.__masonryLayout);
        }
        return changed;
    }

    // Called after a one-off full-canvas paint (page load / history restore) so
    // the NEXT reconcileCanvas() call knows these widgets are already up to date
    // instead of treating every one of them as new (and stutter-replacing them).
    function seedWidgetSnapshots(container) {
        container.querySelectorAll('.widget-container').forEach(w => {
            if (w.id) widgetSourceSnapshots.set(w.id, w.cloneNode(true));
        });
    }

    const CANVAS_DOMPURIFY_CONFIG = {
        // 'script' is allowed because custom create_widget widgets carry their
        // behavior inline; the canvas already trusts Alpine attrs (@click,
        // x-data) which are equivalent execution surface. Scripts are revived
        // by reviveScripts() after injection since innerHTML never runs them.
        ADD_TAGS: ['iframe', 'template', 'script'],
        ADD_ATTR: [
            'style', 'class', 'type', 'checked', 'data-component', 'x-data',
            'x-show', 'x-model', 'x-text', 'x-bind', 'x-on:click', '@click',
            'x-transition', 'x-cloak', 'x-init', 'x-ref', 'x-for', ':class',
            ':style', 'id', 'placeholder', 'value', 'x-if', ':src', ':key',
            ':disabled', 'allow', 'allowfullscreen', 'sandbox',
            'target', 'rel', 'loading'
        ],
        FORCE_BODY: true
    };

    // Returns true if it actually painted, so the caller knows to (re)initialize
    // the widgets it just injected.
    function renderContent(textContent, componentHtml, version) {
        // The text content goes to TTS and Chat History.
        // We ONLY render the HTML component to the live canvas.
        if (!componentHtml || componentHtml === lastRenderedComponentHtml) return false;

        // Server canvas commits are versioned and monotonic. With several turns
        // in flight, a slow turn's component event can arrive after a faster turn
        // already committed a newer canvas — painting the older one would quietly
        // delete the newer widget. Never go backwards.
        if (typeof version === "number") {
            if (version < canvasVersion) return false;
            canvasVersion = version;
        }

        lastRenderedComponentHtml = componentHtml;
        return reconcileCanvas(elements.liveCanvas, componentHtml);
    }

    // ─── HISTORY & PERSISTENCE LOGIC ────────────────────────────────
    async function loadHistory() {
        try {
            const res = await fetch(`/session/${state.sessionId}/history`);
            if (!res.ok) return;
            const data = await res.json();
            // Adopt the server's canvas version: our snapshot now reflects every
            // commit up to it, so the server won't judge our next current_canvas
            // stale (see _run_turn's stale-snapshot guard).
            if (data.canvas_version) canvasVersion = data.canvas_version;
            if (data.messages && data.messages.length > 0) {
                // Populate chat history panel
                if (elements.chatHistoryMessages) elements.chatHistoryMessages.innerHTML = "";
                data.messages.forEach(msg => {
                    if (msg.content !== "[tool-only turn]" && (msg.content || "").trim()) {
                        appendChatMessageToHistory(msg.role, msg.content, Boolean(msg.canvas_html));
                    }
                });

                // Restore the canvas from the last assistant message that carried one
                const assistantMessages = data.messages.filter(m => m.role === "assistant" && (m.canvas_html || m.content !== "[tool-only turn]"));
                if (assistantMessages.length > 0) {
                    const lastMsg = assistantMessages[assistantMessages.length - 1];
                    // canvas_html is split out by the server; fall back to parsing
                    // content for history saved before that change.
                    let temp = document.createElement("div");
                    temp.innerHTML = lastMsg.canvas_html || lastMsg.content;
                    
                    let gridElement = temp.querySelector("#dashboard-grid");
                    
                    if (gridElement) {
                        // Apply filters inside the grid
                        let widgets = gridElement.querySelectorAll(".widget-container, .glass-card, .canvas-element, .rendered-component");
                        widgets.forEach(c => {
                            if (c.textContent.includes("Unknown widget type:")) c.remove();
                            if (c.id && window.WidgetManager && window.WidgetManager.isDismissed(c.id)) c.remove();
                        });
                        
                        elements.liveCanvas.innerHTML = DOMPurify.sanitize(gridElement.outerHTML, CANVAS_DOMPURIFY_CONFIG);
                        window.WidgetLayout.apply(elements.liveCanvas.querySelector('#dashboard-grid'));
                        seedWidgetSnapshots(elements.liveCanvas);
                        // Masonry measures row spans NOW, before restored <img>/<iframe>
                        // content has height. Without re-running on each media load the
                        // grid keeps first-paint spans and taller widgets overlap the
                        // ones below — the "widgets stack on refresh" bug. The live
                        // render path already does this; the restore path never did.
                        relayoutOnMediaSettle(elements.liveCanvas);
                        renderDynamicComponents(elements.liveCanvas);
                    } else {
                        // Fallback for older saved history without #dashboard-grid
                        let components = temp.querySelectorAll(".widget-container, .glass-card, .canvas-element, .rendered-component");
                        let htmlOnly = "";
                        components.forEach(c => {
                            if (c.textContent.includes("Unknown widget type:")) return;
                            if (c.id && window.WidgetManager && window.WidgetManager.isDismissed(c.id)) return;
                            htmlOnly += c.outerHTML;
                        });
                        
                        if (htmlOnly) {
                            elements.liveCanvas.innerHTML = `<div id="dashboard-grid" class="dashboard-grid">${DOMPurify.sanitize(htmlOnly, CANVAS_DOMPURIFY_CONFIG)}</div>`;
                            window.WidgetLayout.apply(elements.liveCanvas.querySelector('#dashboard-grid'));
                            seedWidgetSnapshots(elements.liveCanvas);
                            relayoutOnMediaSettle(elements.liveCanvas);
                            renderDynamicComponents(elements.liveCanvas);
                        }
                    }
                }
            }
        } catch (err) {
            console.error("Failed to load history:", err);
        }
    }

    function getCleanedCanvasHtml() {
        // Force any running glitch to its FINAL text first. The server adopts
        // this HTML as the canonical canvas, so serializing mid-animation would
        // persist scrambled glyphs as the widget's real content — and change its
        // data-sig, making an unchanged widget look changed on every later diff.
        finishGlitches();
        // Same hazard as a mid-glitch serialize: `is-pending-reveal` baked into
        // the canonical canvas would persist an invisible widget forever.
        revealAllPending();
        const temp = document.createElement("div");
        temp.innerHTML = elements.liveCanvas.innerHTML;
        // Same class of hazard: a provisional "composing…" badge serialized
        // into the canonical canvas would persist a permanently-loading card.
        temp.querySelectorAll('[data-provisional]')
            .forEach(el => el.removeAttribute('data-provisional'));
        // The same hazard in its worst form. An envelope is the UI for a turn
        // that is still running; the server ADOPTS this HTML as the canonical
        // canvas, so leaking one in would persist a card that loads forever and
        // survives every reload. Remove the NODE, not just an attribute — unlike
        // a provisional widget there is no real content underneath to keep.
        temp.querySelectorAll('[data-turn-envelope]').forEach(el => el.remove());
        
        // Remove dynamically generated iframes inside youtube player widgets
        const youtubeIframes = temp.querySelectorAll('[x-data*="youtubePlayerWidget"] iframe');
        youtubeIframes.forEach(iframe => iframe.remove());
        
        // Remove dynamically generated list items in checklists (keep fallback text and close button)
        const checklistItems = temp.querySelectorAll('[x-data*="checklistWidget"] ul li');
        checklistItems.forEach(li => {
            if (!li.getAttribute('x-show') && !li.classList.contains('close-widget-btn')) {
                li.remove();
            }
        });

        // Strip EVERY x-for expansion generically. Alpine expands
        // <template x-for> into real sibling nodes after the template; if we
        // serialize those, the server adopts them as canonical HTML and the
        // next Alpine.initTree evaluates their loop-scoped bindings
        // (`:class="r === ..."`, `x-text="row.label"`) OUTSIDE any x-for
        // scope — hundreds of "r is not defined" errors per re-init, plus
        // duplicated nodes when the template re-expands. This caught the
        // stock card's range buttons + metric rows; the youtube/checklist
        // blocks above predate it and keep their extra rules.
        //
        // Generated nodes are inserted contiguously after the template
        // (nested x-if expansion included), so: remove siblings until the
        // first STATIC one. Audited invariant of every factory template:
        // static siblings after an x-for all carry x-show (empty-state
        // fallbacks, loading spinners) or are the checklist close button —
        // anything else after an x-for template is Alpine output.
        temp.querySelectorAll('template[x-for]').forEach(tpl => {
            let n = tpl.nextElementSibling;
            while (n) {
                if (n.hasAttribute('x-show') || n.hasAttribute('x-for')
                    || n.classList.contains('close-widget-btn')) {
                    break;
                }
                const gone = n;
                n = n.nextElementSibling;
                gone.remove();
            }
        });

        // Strip legacy dataset listener tags that were serialized to HTML
        const allElements = temp.querySelectorAll('[data-has-listener]');
        allElements.forEach(el => el.removeAttribute('data-has-listener'));

        // Strip the script-revival marker. It is client-side execution state,
        // not content — serialized into the canonical canvas it told the NEXT
        // page load "this script already ran" in a document where it never did,
        // so every create_widget custom widget went permanently dead after one
        // adopt+reload cycle.
        temp.querySelectorAll('script[data-revived]').forEach(s => s.removeAttribute('data-revived'));

        // Strip rendered chart canvases + their marker, keeping the hidden
        // config <pre> as the persisted source of truth: a <canvas> serializes
        // empty, and renderDynamicComponents rebuilds the live chart from the
        // config block on the next paint/load.
        temp.querySelectorAll('.chart-container').forEach(el => el.remove());
        temp.querySelectorAll('[data-chart-rendered]').forEach(el => el.removeAttribute('data-chart-rendered'));
        // Same treatment for the holder-network graph: drop the live cytoscape
        // mount and clear the marker so the hidden language-graph config block is
        // the persisted source of truth and the graph rebuilds on next load.
        temp.querySelectorAll('.graph-canvas').forEach(el => el.remove());
        temp.querySelectorAll('[data-graph-rendered]').forEach(el => el.removeAttribute('data-graph-rendered'));

        // Error banners are transient UI, never canvas content.
        temp.querySelectorAll('.system-error-banner').forEach(el => el.remove());

        // The music player's height lives in an Alpine :class binding
        // (showQueue ? h-[420px] : h-[280px]); Alpine merges the RESOLVED class
        // into the class attribute, so serializing while the queue is open baked
        // h-[420px] as a STATIC class that Alpine (which only removes classes it
        // added in this document) never clears after rehydrate — a stuck
        // double-height card. Strip both; the binding re-applies the right one.
        temp.querySelectorAll('[x-data*="musicPlayerWidget"]').forEach(el => {
            el.classList.remove('h-[280px]', 'h-[420px]');
        });

        // crt-on/crt-off are transient power-on/off animation classes. If a
        // request goes out mid-animation they'd otherwise get baked into the
        // canvas the server treats as canonical — permanently, since nothing
        // ever removes a class from server-persisted markup — which then
        // reads as "this widget changed" on every future diff and forces an
        // unnecessary (and visibly stutter-y) one-time re-render of it.
        // is-focused (follow-up focus ring) and is-entering/is-updating (the
        // change-flash animations) are transient for the same reason and would
        // bake in identically — nothing ever removes a class from
        // server-persisted markup.
        temp.querySelectorAll('.crt-on, .crt-off, .is-focused, .is-entering, .is-updating')
            .forEach(el => {
                el.classList.remove('crt-on', 'crt-off', 'is-focused',
                                    'is-entering', 'is-updating');
            });

        // Legacy: the word-span reveal that preceded the glitch could leave
        // span.tw-word in a canvas saved while it was live. Nothing produces them
        // any more, but stripping them keeps an old saved canvas from carrying
        // meaningless markup forward for the rest of its life.
        temp.querySelectorAll('span.tw-word').forEach(s => {
            s.replaceWith(document.createTextNode(s.textContent));
        });
        temp.normalize();

        // Strip client-computed LAYOUT styles before persisting. The masonry script
        // writes an inline `grid-row-end: span <px>` onto every widget; if that span
        // is baked into the saved canvas it re-applies on the next load BEFORE the
        // masonry script recomputes — and a span that was measured while images were
        // still loading (too short) then overrides the CSS anti-overlap default, so
        // the widget below rides up over it (the "widgets stack on refresh" bug).
        // grid-column / height come from the resizer, which restores them from its
        // own localStorage, so dropping them here loses nothing. Non-layout inline
        // styles (from server templates) are kept.
        temp.querySelectorAll('.widget-container, .glass-card').forEach(el => {
            if (!el.style) return;
            ['grid-row-end', 'grid-row', 'grid-column', 'height'].forEach(p =>
                el.style.removeProperty(p));
            if (!el.getAttribute('style')) el.removeAttribute('style');
        });

        // Drag handles / resize handles are injected client-side; never persist them.
        temp.querySelectorAll('.widget-move-handle, .widget-resize-handle')
            .forEach(h => h.remove());

        return temp.innerHTML;
    }

    // ─── CHAT & RENDERING LOGIC ────────────────────────────────
    // Several turns run at once (the Spark serves multiple generations in
    // parallel); anything past MAX_CONCURRENT_TURNS waits in the queue.
    //
    // The canvas is the shared resource, and it is the server's copy that is
    // authoritative: every widget write is a locked read-modify-write there, and
    // each committed canvas carries a version. We only paint a canvas newer than
    // what's on screen, so a slow turn's component event landing late can't roll
    // back a widget a faster turn already committed. Before this, two turns each
    // replaced the canvas wholesale from their own stale snapshot and the loser's
    // widget silently vanished — both queries looked like they'd been knocked out.
    const MAX_CONCURRENT_TURNS = 3;
    const chatQueue = [];
    const activeTurns = new Set();
    let canvasVersion = 0;

    function updateQueueIndicator() {
        if (!elements.queueBadge) return;
        const queued = chatQueue.length;
        const running = activeTurns.size;
        const parts = [];
        if (running > 1) parts.push(`${running} running`);
        if (queued) parts.push(`${queued} queued`);
        elements.queueBadge.textContent = parts.join(" · ");
        elements.queueBadge.style.display = parts.length ? "inline-flex" : "none";
    }

    // One slim progress bar per in-flight query, stacked above the command bar.
    // The server never reports a completion percentage, so the fill is honest
    // about what it knows: server events bump it to stage milestones, a slow
    // asymptotic creep keeps it visibly alive between events, and the ticking
    // elapsed timer is the real "how long is this taking" signal.
    function createTurnStatus(text) {
        const container = elements.turnStatusContainer;
        if (!container) return { stage() {}, finish() {} };
        container.style.display = "flex";
        const row = document.createElement("div");
        row.className = "turn-status";
        row.innerHTML =
            '<div class="turn-status-top">' +
            '<span class="turn-status-label"></span>' +
            '<span class="turn-status-stage">Connecting…</span>' +
            '<span class="turn-status-elapsed">0s</span>' +
            '</div>' +
            '<div class="turn-status-track"><div class="turn-status-fill"></div></div>';
        row.querySelector(".turn-status-label").textContent =
            text.length > 60 ? `${text.slice(0, 60)}…` : text;
        container.appendChild(row);

        const stageEl = row.querySelector(".turn-status-stage");
        const elapsedEl = row.querySelector(".turn-status-elapsed");
        const fillEl = row.querySelector(".turn-status-fill");
        const startedAt = performance.now();
        let progress = 4;
        fillEl.style.width = `${progress}%`;

        const fmtElapsed = () => {
            const secs = (performance.now() - startedAt) / 1000;
            return secs < 60 ? `${Math.floor(secs)}s`
                             : `${Math.floor(secs / 60)}m ${Math.floor(secs % 60)}s`;
        };
        const ticker = setInterval(() => {
            elapsedEl.textContent = fmtElapsed();
            progress = Math.min(90, progress + (90 - progress) * 0.015);
            fillEl.style.width = `${progress}%`;
        }, 250);

        let finished = false;
        return {
            stage(msg, milestone) {
                if (finished || !msg) return;
                stageEl.textContent = msg.length > 52 ? `${msg.slice(0, 52)}…` : msg;
                if (milestone) progress = Math.max(progress, Math.min(92, milestone));
            },
            finish(kind, msg) {
                if (finished) return;
                finished = true;
                clearInterval(ticker);
                fillEl.style.width = "100%";
                row.classList.add(kind === "error" ? "is-error"
                                : kind === "stopped" ? "is-stopped" : "is-done");
                stageEl.textContent = msg || (kind === "error" ? "Failed"
                                            : kind === "stopped" ? "Stopped" : "Done");
                elapsedEl.textContent = fmtElapsed();
                // Leave failures on screen longer — they carry information.
                setTimeout(() => {
                    row.remove();
                    if (!container.children.length) container.style.display = "none";
                }, kind === "done" ? 3500 : 8000);
            }
        };
    }

    // ─── IN-FLIGHT ENVELOPE ────────────────────────────────
    // A fast-lane query (clock, weather, a video) lands in a second or two and
    // needs no ceremony. An agent research turn takes 30-120s, and for that whole
    // window the only feedback used to be a 4px bar creeping on a fake timer at
    // the bottom of the screen — which reads as stuck, not as working.
    //
    // So a turn now leaves the command bar as a piece of mail and lands on the
    // canvas as a card that says what the agent is actually doing, in the place
    // the answer will appear. THREE lanes, and the split is the whole anti-flood
    // rule: a PHASE that changes about five times a turn, ONE detail line that is
    // replaced rather than appended, and a progress bar with a real denominator.
    // Everything raw — MCP tool names, args, errors — stays in the Activity log,
    // which is unchanged and already expandable.
    //
    // The card never grows. If you are tempted to append a second line, put it in
    // the Activity log instead.

    // How long a turn may stay quiet before it earns a card, when the server has
    // not (yet) told us which path it took. The fast lane's slowest members —
    // weather, a video search — settle inside this, so they never spawn one.
    const ENVELOPE_EXPAND_AFTER_MS = 2500;
    // Floor between two visible detail-line updates. Prism can fire tool events
    // in bursts; without this the line flickers unreadably and reads as noise
    // rather than progress.
    const ENVELOPE_DETAIL_MIN_MS = 600;

    const ENVELOPE_PHASES = {
        sent:        "Sent to the agent",
        routing:     "Reading your question",
        researching: "Researching",
        reading:     "Reading sources",
        composing:   "Composing"
    };

    // Bare tool name -> how to say it out loud. Keys are matched AFTER the
    // mcp__lazy-tool-service__ prefix is stripped. `arg` names the args field
    // worth showing; the server only ever sends a summarized handful.
    const TOOL_LABELS = {
        html_notes_web_search:    { verb: "searching",      arg: "query" },
        html_notes_news:          { verb: "pulling news",   arg: "topic" },
        html_notes_stock_news:    { verb: "stock news",     arg: "ticker" },
        html_notes_stock_history: { verb: "checking",       arg: "ticker" },
        html_notes_sports_scores: { verb: "checking scores", arg: "league" },
        html_notes_get_weather:   { verb: "checking weather", arg: "location" },
        html_notes_youtube_search: { verb: "finding video", arg: "query" },
        html_notes_read_page:     { verb: "reading",        arg: "url", host: true },
        html_notes_search_notes:  { verb: "searching notes", arg: "query" },
        canvas_read_dom:          { verb: "reading the canvas" },
        canvas_add_widget:        { verb: "building the card" },
        canvas_modify_dom:        { verb: "updating the canvas" },
        create_widget:            { verb: "building the card" },
        update_widget:            { verb: "updating the card" },
        plan_widget:              { verb: "planning the card" },
        validate_widget_html:     { verb: "checking the card" }
    };

    // "mcp__lazy-tool-service__html_notes_web_search" + {query:"nvidia q3"}
    //   -> "searching: nvidia q3"
    // The raw name is never shown here — it is already in the Activity log, and
    // an MCP-prefixed identifier is not a progress report.
    function humanizeTool(tool, args) {
        const bare = String(tool || "").split("__").pop();
        const spec = TOOL_LABELS[bare];
        const verb = spec ? spec.verb
                          : bare.replace(/^html_notes_/, "").replace(/_/g, " ");
        if (!spec || !spec.arg || !args) return verb;
        let val = args[spec.arg];
        if (typeof val !== "string" || !val.trim()) return verb;
        val = val.trim();
        if (spec.host) {
            // A full URL is mostly noise; the site is the informative part.
            try { val = new URL(val).hostname.replace(/^www\./, ""); } catch (e) { /* keep raw */ }
        }
        return `${verb}: ${val.length > 48 ? val.slice(0, 48) + "…" : val}`;
    }

    // One envelope per turn. Starts as a pip in the status bar's own row and only
    // becomes a canvas card once the turn is known to be slow — see `maybeExpand`.
    function createTurnEnvelope(text) {
        const grid = () => elements.liveCanvas && elements.liveCanvas.querySelector("#dashboard-grid");

        let node = null;              // the canvas card, once expanded
        let phaseEl, detailEl, elapsedEl, fillEl;
        let expanded = false, finished = false, sawComponent = false;
        let currentPhase = "sent";
        const startedAt = performance.now();

        // Detail-line throttle. `queued` holds the newest text seen during a
        // cooldown so a burst shows its LAST state rather than its first.
        let lastDetailAt = 0, queued = null, queuedTimer = null;
        let lastDetailText = "", repeatCount = 1;

        const expandTimer = setTimeout(() => maybeExpand("slow"), ENVELOPE_EXPAND_AFTER_MS);

        function fmtElapsed() {
            const secs = (performance.now() - startedAt) / 1000;
            return secs < 60 ? `${Math.floor(secs)}s`
                             : `${Math.floor(secs / 60)}m ${Math.floor(secs % 60)}s`;
        }

        const ticker = setInterval(() => {
            if (elapsedEl) elapsedEl.textContent = fmtElapsed();
        }, 500);

        function build() {
            const g = grid();
            if (!g) return false;
            node = document.createElement("div");
            // No id, and none of the widget classes. Both are deliberate: an id
            // would be captured into the saved layout order, and a widget class
            // would put a transient card in front of every sweep that assumes a
            // real widget. See the masonry note in index.html.
            node.className = "turn-envelope";
            node.setAttribute("data-turn-envelope", "");
            node.innerHTML =
                '<div class="turn-envelope-head">' +
                  '<span class="turn-envelope-stamp" aria-hidden="true">✉</span>' +
                  '<span class="turn-envelope-query"></span>' +
                  '<span class="turn-envelope-elapsed">0s</span>' +
                '</div>' +
                '<div class="turn-envelope-phase"></div>' +
                '<div class="turn-envelope-detail"></div>' +
                '<div class="turn-envelope-track"><div class="turn-envelope-fill"></div></div>';
            node.querySelector(".turn-envelope-query").textContent =
                text.length > 64 ? `${text.slice(0, 64)}…` : text;
            phaseEl = node.querySelector(".turn-envelope-phase");
            detailEl = node.querySelector(".turn-envelope-detail");
            elapsedEl = node.querySelector(".turn-envelope-elapsed");
            fillEl = node.querySelector(".turn-envelope-fill");
            phaseEl.textContent = ENVELOPE_PHASES[currentPhase] || ENVELOPE_PHASES.sent;
            elapsedEl.textContent = fmtElapsed();
            // Appended last so the widget that reconcileCanvas appends next lands
            // adjacent to it — the card is replaced by its own answer, in place.
            g.appendChild(node);
            // Flight: the card is drawn at the command bar and travels to its
            // slot. Measured, not hardcoded, so it survives a layout change.
            const bar = elements.chatInput;
            if (bar && node.getBoundingClientRect) {
                const from = bar.getBoundingClientRect();
                const to = node.getBoundingClientRect();
                if (to.width) {
                    node.style.setProperty("--fly-x", `${(from.left + from.width / 2) - (to.left + to.width / 2)}px`);
                    node.style.setProperty("--fly-y", `${(from.top + from.height / 2) - (to.top + to.height / 2)}px`);
                }
            }
            node.classList.add("is-arriving");
            node.addEventListener("animationend", function once() {
                node.classList.remove("is-arriving");
                node.removeEventListener("animationend", once);
            }, { once: true });
            if (window.__masonryLayout) requestAnimationFrame(window.__masonryLayout);
            return true;
        }

        // Expand on the SERVER saying this is an agent turn, or on the turn simply
        // taking too long. The server's word arrives after routing, which is
        // exactly the window that feels stuck — so the timer is not a fallback,
        // it is what covers the gap the `debug` event cannot.
        function maybeExpand(why) {
            if (expanded || finished || sawComponent) return;
            expanded = true;
            clearTimeout(expandTimer);
            if (!build()) { expanded = false; return; }
            HN.log("envelope", `expanded (${why})`);
        }

        function paintDetail(msg) {
            if (!detailEl) return;
            detailEl.textContent = repeatCount > 1 ? `${msg} · ${repeatCount}` : msg;
            detailEl.classList.remove("is-fresh");
            void detailEl.offsetWidth;   // restart the fade
            detailEl.classList.add("is-fresh");
        }

        return {
            node: () => node,
            expand: () => maybeExpand("server"),

            phase(name) {
                // An untagged status leaves the phase alone rather than guessing.
                // That is why `thinking` carries no phase server-side: it happens
                // *within* a phase, and letting it set one bounced the card
                // backwards between "reading" and "researching".
                if (!name || finished || !ENVELOPE_PHASES[name]) return;
                currentPhase = name;
                if (phaseEl) phaseEl.textContent = ENVELOPE_PHASES[name];
            },

            detail(msg) {
                if (!msg || finished) return;
                // A repeat is a count, not a new line. Four searches in a row are
                // four searches, not four identical lines flickering past.
                if (msg === lastDetailText) repeatCount += 1;
                else { lastDetailText = msg; repeatCount = 1; }

                const now = performance.now();
                const wait = ENVELOPE_DETAIL_MIN_MS - (now - lastDetailAt);
                if (wait <= 0) {
                    lastDetailAt = now;
                    paintDetail(msg);
                    return;
                }
                queued = msg;
                if (queuedTimer) return;      // a flush is already scheduled
                queuedTimer = setTimeout(() => {
                    queuedTimer = null;
                    if (finished || queued === null) return;
                    lastDetailAt = performance.now();
                    paintDetail(queued);
                    queued = null;
                }, wait);
            },

            // A REAL fraction from the server. The bar only ever moves forward:
            // prism's iteration counter and the research budget both report here
            // and they run at different rates, so a raw assignment would visibly
            // rewind mid-turn.
            progress(step, of) {
                if (!fillEl || finished || !of) return;
                const pct = Math.max(6, Math.min(94, (step / of) * 100));
                const cur = parseFloat(fillEl.style.width) || 0;
                if (pct > cur) fillEl.style.width = `${pct}%`;
            },

            // The answer has landed. Fold the card away and let the real widget
            // take the slot it was holding.
            handoff() {
                sawComponent = true;
                clearTimeout(expandTimer);
                if (!node || finished) return;
                finished = true;
                clearInterval(ticker);
                if (queuedTimer) clearTimeout(queuedTimer);
                node.classList.add("is-delivered");
                const drop = () => { if (node && node.parentElement) node.remove();
                                     if (window.__masonryLayout) requestAnimationFrame(window.__masonryLayout); };
                node.addEventListener("animationend", drop, { once: true });
                // Never leave a card on the canvas because an animation did not
                // fire (reduced-motion, a backgrounded tab).
                setTimeout(drop, 900);
            },

            // Stop / error / a turn that ended without ever painting a widget.
            finish(kind) {
                clearTimeout(expandTimer);
                if (finished) return;
                // A turn can end cleanly having only spoken (no widget), so `done`
                // still has to retire the card — hand it off before latching
                // `finished`, which handoff() itself checks.
                if (kind === "done") { this.handoff(); return; }
                finished = true;
                clearInterval(ticker);
                if (queuedTimer) clearTimeout(queuedTimer);
                if (!node) return;
                node.classList.add(kind === "error" ? "is-error" : "is-stopped");
                if (phaseEl) phaseEl.textContent = kind === "error" ? "Failed" : "Stopped";
                // Failures carry information — leave them up a beat longer, the
                // same bargain the status bar already makes.
                setTimeout(() => { if (node && node.parentElement) node.remove();
                                   if (window.__masonryLayout) requestAnimationFrame(window.__masonryLayout); }, 4000);
            }
        };
    }

    function sendChatMessage() {
        const text = elements.chatInput.value.trim();
        if (!text) return;

        elements.chatInput.value = "";
        elements.chatInput.style.height = 'auto';

        // Echo immediately so a queued message still shows up straight away.
        appendChatMessageToHistory("user", text);
        chatQueue.push(text);
        updateQueueIndicator();
        drainChatQueue();
    }

    // Programmatic ask: widget chrome (a stock chart's legend, etc.) drives the
    // same queue the chat box uses, so the turn gets history, echo and the
    // stale-snapshot guard for free. Clears the widget-focus hint first — the
    // click that triggered this landed INSIDE a widget, and letting it ride as
    // focus_widget_id would invite the server to edit that widget in place
    // instead of spawning the asked-for one.
    window.HN.ask = function (text) {
        text = String(text || "").trim();
        if (!text) return;
        state.focusWidgetId = null;
        appendChatMessageToHistory("user", text);
        chatQueue.push(text);
        updateQueueIndicator();
        drainChatQueue();
    };

    function drainChatQueue() {
        while (chatQueue.length && activeTurns.size < MAX_CONCURRENT_TURNS) {
            const text = chatQueue.shift();
            const controller = new AbortController();
            activeTurns.add(controller);
            updateQueueIndicator();

            runChatTurn(text, controller)
                .catch(err => {
                    if (err.name !== "AbortError") console.error("Chat turn failed:", err);
                })
                .finally(() => {
                    activeTurns.delete(controller);
                    updateQueueIndicator();
                    drainChatQueue();
                });
        }
    }

    async function runChatTurn(text, controller) {
        // Only wipe the shared speech queue when this is the ONLY turn running.
        // Unconditionally clearing here meant that firing three questions in
        // quick succession cut question 1's spoken answer off mid-sentence the
        // instant question 2 launched — the answer had arrived, it just never
        // finished being read. Same rule the exec-log already uses below.
        if (activeTurns.size <= 1) clearSpeechQueue();

        let provider = "vllm-2";
        let model = "";
        if (elements.modelSelect) {
            let selectValue = elements.modelSelect.value;
            if (!selectValue && elements.modelSelect.options.length > 0) {
                // Default to the first available model in the dropdown if none selected
                selectValue = elements.modelSelect.options[0].value;
            }
            if (selectValue) {
                try {
                    const selected = JSON.parse(selectValue);
                    provider = selected.provider;
                    model = selected.model;
                } catch (e) {
                    console.error("Failed to parse model select value", e);
                }
            }
        }

        // Each turn owns a group inside the shared activity log — concurrent turns
        // would otherwise scribble over one another's steps. Only wipe the log
        // when this is the only turn running.
        if (activeTurns.size <= 1) elements.execLogContent.innerHTML = "";
        elements.execLogContainer.style.display = "flex";

        const logGroup = document.createElement("div");
        logGroup.className = "log-group";
        if (activeTurns.size > 1) {
            const label = document.createElement("div");
            label.className = "log-group-label";
            label.textContent = text.length > 42 ? `${text.slice(0, 42)}…` : text;
            logGroup.appendChild(label);
        }
        elements.execLogContent.appendChild(logGroup);

        let lastStatusStep = null;
        function addLogStep(text, icon) {
            if (icon === "🧠") {
                const cleanText = text.replace(/\.+$/, "");
                if (lastStatusStep) {
                    const stepText = lastStatusStep.querySelector(".step-text");
                    if (stepText) {
                        stepText.innerHTML = cleanText;
                    }
                    return;
                }
                const step = document.createElement("div");
                step.className = "log-step status-step";
                step.innerHTML = `<span class="step-icon">${icon}</span><span class="step-text">${cleanText}</span><span class="dot-flashing ml-2 inline-block"></span>`;
                logGroup.appendChild(step);
                lastStatusStep = step;
                elements.execLogContent.scrollTop = elements.execLogContent.scrollHeight;
                return;
            }

            lastStatusStep = null;
            const step = document.createElement("div");
            step.className = "log-step";
            step.innerHTML = `<span class="step-icon">${icon}</span><span class="step-text">${text}</span>`;
            logGroup.appendChild(step);
            elements.execLogContent.scrollTop = elements.execLogContent.scrollHeight;
        }

        HN.turn(text);
        addLogStep("Connecting to agent...", "🔗");
        const statusBar = createTurnStatus(text);
        // Created for EVERY turn but silent until the turn proves slow — a clock
        // query never draws one. See ENVELOPE_EXPAND_AFTER_MS.
        const envelope = createTurnEnvelope(text);

        if (elements.btnSendMessage) elements.btnSendMessage.style.display = "none";
        if (elements.btnStopMessage) elements.btnStopMessage.style.display = "flex";

        // Each turn carries its own controller, so aborting one can't orphan
        // another. state.abortController mirrors the newest for the Stop button.
        state.abortController = controller;

        try {
            const res = await fetch("/session/message", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: controller.signal,
                body: JSON.stringify({
                    session_id: state.sessionId,
                    message: text,
                    provider: provider,
                    model: model,
                    current_canvas: getCleanedCanvasHtml(),
                    // The widget this question came from, when we know it. A
                    // fact beats the server's text-based inference — but only
                    // if it's still on the canvas (it may have been dismissed).
                    focus_widget_id: (state.focusWidgetId &&
                        elements.liveCanvas.querySelector(`#${CSS.escape(state.focusWidgetId)}`))
                        ? state.focusWidgetId : null,
                    // The version this snapshot is based on — lets the server
                    // refuse it if another turn committed since (stale-snapshot
                    // guard in _run_turn; without it a fast sibling turn's
                    // widget gets silently wiped).
                    canvas_version: canvasVersion
                    // use_lazy_agent is deliberately NOT sent — the SERVER decides
                    // (MessageRequest defaults it to False = PRISM MODE).
                    //
                    // It used to be pinned to `true` (the :5591 fork) because prism
                    // dropped the mcp__lazy-tool-service__* widget tools ("not in
                    // schema") and never rendered. That premise is dead:
                    // lazy-tool-service is now a CONNECTED MCP server in prism, so
                    // prism serves all html_notes_*/canvas_* tools and runs the real
                    // research harnesses (verified live). Hardcoding it here silently
                    // overrode the server default and forced every browser turn back
                    // onto the local search-scrape builders — keep this un-sent so
                    // there is ONE source of truth for the routing mode.
                })
            });

            if (!res.ok) {
                console.error("Error from API:", await res.text());
                renderError("Failed to process request. See console.");
                statusBar.finish("error", `Request failed (${res.status})`);
                envelope.finish("error");
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let done = false;
            let fullText = "";
            let fullComponentHtml = "";
            const liveBubble = createLiveAssistantBubble();

            // SSE lines can be split across reader.read() boundaries — and the
            // `component` event's payload IS the whole canvas (often many KB), so
            // it is split almost every time. Splitting each raw chunk on "\n" and
            // JSON.parse-ing immediately meant a half-arrived component line failed
            // to parse (swallowed as a "partial chunk"), its continuation didn't
            // start with "data: " so it was ignored too, and the widget silently
            // never painted until a reload repainted it from history. Buffer across
            // reads and only ever process COMPLETE, newline-terminated lines.
            let buffer = "";

            // Agent-driven app open (App Hub). window.open from an SSE callback
            // has no user gesture, so most browsers popup-block it — the
            // clickable toast is the guaranteed path; the direct open is a
            // bonus when the browser allows it.
            const showOpenLinkToast = (url, name) => {
                const toast = document.createElement("div");
                toast.style.cssText =
                    "position:fixed;bottom:24px;right:24px;z-index:9999;" +
                    "background:rgba(15,23,42,.95);color:#fff;padding:14px 18px;" +
                    "border-radius:16px;border:1px solid rgba(168,85,247,.5);" +
                    "box-shadow:0 8px 30px rgba(0,0,0,.5);font-size:14px;" +
                    "display:flex;align-items:center;gap:10px;max-width:340px;";
                const link = document.createElement("a");
                link.href = url;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                link.textContent = `🚀 Open ${name || url}`;
                link.style.cssText = "color:#c084fc;font-weight:600;text-decoration:none;";
                link.addEventListener("click", () => toast.remove());
                const close = document.createElement("button");
                close.textContent = "✕";
                close.style.cssText = "background:none;border:none;color:#94a3b8;cursor:pointer;font-size:12px;";
                close.addEventListener("click", () => toast.remove());
                toast.append(link, close);
                document.body.appendChild(toast);
                setTimeout(() => toast.remove(), 20000);
            };

            const dispatch = (data) => {
                if (data.type === "chunk") {
                    const token = data.content || "";
                    // The server tags a generated summary with the widget that
                    // sentence DESCRIBES. Hold it so the sentence assembled from
                    // this chunk reveals THAT widget rather than whichever happens
                    // to be next in the queue.
                    if (data.widget_id) pendingSentenceWidgetId = data.widget_id;
                    fullText += token;
                    // Deliberately does NOT repaint the canvas: this
                    // turn's snapshot may already be older than what a
                    // sibling turn committed. Only `component` paints.
                    handleIncomingChunk(token);
                    liveBubble.append(fullText);
                } else if (data.type === "status") {
                    HN.log("status", data.message);
                    addLogStep(data.message || "Thinking...", "🧠");
                    statusBar.stage(data.message || "Thinking…", 40);
                    // `phase` is authoritative and optional — an untagged status
                    // (a `thinking` event, a tool error) leaves the phase alone.
                    envelope.phase(data.phase);
                } else if (data.type === "progress") {
                    // A real fraction from the server, replacing the fake creep.
                    envelope.progress(data.step, data.of);
                } else if (data.type === "debug") {
                    // Server routing breadcrumb — which path/widget the query hit.
                    // Surfaced in DevTools so a misroute is visible without server logs.
                    HN.route(data);
                    // The one field that says "this turn will be slow". It arrives
                    // as the first frame of the agent path, which is the earliest
                    // anything can honestly say so.
                    if (data.path === "agent") envelope.expand();
                } else if (data.type === "done") {
                    HN.log("done", "generation finished");
                    HN.groupEnd();
                    // Belt-and-braces: no widget should still read "composing…"
                    // after the turn ends (server promotion normally clears it;
                    // this covers an aborted or error-shortened turn).
                    elements.liveCanvas.querySelectorAll('[data-provisional]')
                        .forEach(el => el.removeAttribute('data-provisional'));
                    renderDynamicComponents(elements.liveCanvas);
                    addLogStep("Finished generation.", "✨");
                    statusBar.finish("done");
                    envelope.finish("done");
                    flushSentenceBuffer();
                    // Finalize the bubble already on screen; only append a fresh
                    // one when nothing ever streamed (so an empty turn still
                    // records its "canvas updated" marker, as it always did).
                    if (!liveBubble.finalize(fullText, Boolean(fullComponentHtml))) {
                        appendChatMessageToHistory("assistant", fullText, Boolean(fullComponentHtml));
                    }
                } else if (data.type === "component") {
                    HN.component(data.content || "");
                    addLogStep("Rendered visual component", "🎨");
                    statusBar.stage("Rendering widget…", 85);
                    // Fold the card away and let the answer take its slot. Done
                    // BEFORE the paint so the two animations read as one handoff.
                    envelope.handoff();
                    fullComponentHtml = data.content || "";
                    if (renderContent(fullText, fullComponentHtml, data.version)) {
                        // Initialize the moment the widget lands, not on
                        // "done". A stock_card is entirely Alpine x-text, so
                        // painted-but-uninitialized is an EMPTY SHELL — and
                        // any turn whose "done" never arrives (abort, second
                        // turn, dropped connection) left it that way until
                        // the page was reloaded.
                        renderDynamicComponents(elements.liveCanvas);
                    }
                    scrollToBottom();
                } else if (data.type === "tool_call") {
                    HN.log("tool", data.tool, data.args || data.input || "");
                    // The Activity log keeps the RAW name and args — full fidelity
                    // lives there. The envelope gets the readable version.
                    addLogStep(`Calling tool: <strong>${data.tool}</strong>...`, "🔧");
                    statusBar.stage(`Tool: ${data.tool}`, 55);
                    envelope.phase(data.phase);
                    envelope.detail(humanizeTool(data.tool, data.args || data.input));
                } else if (data.type === "open_url") {
                    HN.log("open_url", data.url);
                    addLogStep(`Opening <strong>${data.name || data.url}</strong> in a new tab…`, "🚀");
                    const win = window.open(data.url, "_blank", "noopener,noreferrer");
                    if (!win) showOpenLinkToast(data.url, data.name);
                } else if (data.type === "error") {
                    HN.error(data.message);
                    addLogStep(`Error: ${data.message}`, "❌");
                    renderError(data.message || "An error occurred.");
                    statusBar.finish("error", data.message || "An error occurred");
                    envelope.finish("error");
                }
            };

            const processLine = (line) => {
                if (!line.startsWith("data: ")) return;
                try {
                    dispatch(JSON.parse(line.substring(6)));
                } catch (e) {
                    // A complete SSE line failed to parse — genuinely malformed,
                    // not a partial (partials never reach here). Log and skip.
                    console.warn("Dropped unparseable SSE line", e);
                }
            };

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    buffer += decoder.decode(value, { stream: true });
                    let newlineIdx;
                    // Process only lines that are fully terminated by "\n". Any
                    // trailing partial line stays in `buffer` for the next read.
                    while ((newlineIdx = buffer.indexOf("\n")) !== -1) {
                        const line = buffer.slice(0, newlineIdx);
                        buffer = buffer.slice(newlineIdx + 1);
                        // Trim a trailing "\r" in case the stream uses CRLF.
                        processLine(line.endsWith("\r") ? line.slice(0, -1) : line);
                    }
                }
            }
            // Flush any final line the stream ended without a trailing newline.
            if (buffer.trim()) processLine(buffer.trim());
            
            // Final cleanup. No canvas repaint — `component` already painted the
            // newest version, and this turn's copy may be behind a sibling's.
            renderDynamicComponents(elements.liveCanvas);
            // Streams that end without a `done` event (dropped connection mid-
            // stream) would otherwise leave the bar spinning forever.
            statusBar.finish("done");
            envelope.finish("done");
            hideLogWhenIdle();

        } catch (err) {
            if (err.name === 'AbortError') {
                console.log("Request was aborted by user.");
                addLogStep("Generation stopped by user.", "🛑");
                statusBar.finish("stopped");
                envelope.finish("stopped");
            } else {
                console.error("Network error:", err);
                renderError("Network error. Is the server running?");
                statusBar.finish("error", "Network error");
                envelope.finish("error");
            }
            hideLogWhenIdle();
        } finally {
            // This turn is still counted in activeTurns until drainChatQueue's
            // .finally() removes it, so "last one out" means size <= 1.
            const lastOneOut = activeTurns.size <= 1 && chatQueue.length === 0;
            if (lastOneOut) {
                if (elements.btnSendMessage) elements.btnSendMessage.style.display = "flex";
                if (elements.btnStopMessage) elements.btnStopMessage.style.display = "none";
                state.abortController = null;
            }
        }
    }

    function hideLogWhenIdle() {
        setTimeout(() => {
            if (activeTurns.size === 0 && chatQueue.length === 0) {
                elements.execLogContainer.style.display = "none";
            }
        }, 3000);
    }

    function renderError(msg) {
        // NEVER wipe the canvas: this used to assign liveCanvas.innerHTML, so one
        // failed turn (an SSE error, an HTTP 500, a network blip) replaced every
        // widget — including ones committed by successful sibling turns — with a
        // single error line. Worse, the next message serialized that empty canvas
        // and the server ADOPTED it as canonical (no new commit had bumped the
        // version), deleting every widget server-side. Surface the error as a
        // transient banner instead; the widgets stay.
        const existing = elements.liveCanvas.querySelector('.system-error-banner');
        if (existing) existing.remove();
        const banner = document.createElement('div');
        banner.className = 'system-message system-error-banner';
        banner.style.cssText = 'color: var(--danger-color); margin: 0.5rem 0;';
        banner.textContent = msg;
        elements.liveCanvas.prepend(banner);
        setTimeout(() => banner.remove(), 10000);
    }

    function scrollToBottom() {
        if (elements.liveCanvas) {
            elements.liveCanvas.scrollTop = elements.liveCanvas.scrollHeight;
        }
    }

    function reviveScripts(container) {
        // Scripts injected via innerHTML never execute — recreate each one so
        // the browser runs it. This is what makes create_widget jsContent work.
        //
        // Only revive scripts that have NOT already been revived: this runs over
        // the whole canvas after every component event, and a widget the
        // reconciler deliberately preserved still holds its already-run script.
        // Re-creating (and thus re-executing) it on every new-widget add would
        // restart whatever it does — a second visible cause of widget stutter.
        // The data-revived marker rides along on the preserved live node, so its
        // script is skipped on every subsequent pass.
        container.querySelectorAll('script:not([data-revived])').forEach(oldScript => {
            const newScript = document.createElement('script');
            for (const attr of oldScript.attributes) {
                newScript.setAttribute(attr.name, attr.value);
            }
            newScript.setAttribute('data-revived', '');
            newScript.textContent = oldScript.textContent;
            oldScript.parentNode.replaceChild(newScript, oldScript);
        });
    }

    function applyImageFallbacks(container) {
        // Any widget image that fails to load degrades to a monogram tile
        // instead of a broken-image icon.
        container.querySelectorAll('.widget-container img, .glass-card img, .canvas-widget img, img').forEach(img => {
            // CRT Turn-On image reveal animation
            if (!img._hasCrt) {
                img._hasCrt = true;
                if (img.complete && img.naturalWidth > 0) {
                    img.classList.add('crt-reveal');
                } else {
                    img.addEventListener('load', () => {
                        img.classList.add('crt-reveal');
                    });
                }
            }

            if (img._hasFallback) return;
            img._hasFallback = true;
            img.addEventListener('error', () => {
                // Defer to the capture-phase gate above, which runs FIRST and may
                // be mid-retry against the source's favicon. Replacing the node
                // here would destroy the element that retry just re-pointed, so a
                // recoverable image became a monogram every time. Only take over
                // once that gate has marked the image definitively failed.
                if (img.dataset.imgRetried === '1' && img.dataset.imgFailed !== '1') return;
                const placeholder = document.createElement('div');
                placeholder.className = img.className + ' img-fallback';
                placeholder.style.minHeight = '3.5rem';
                const label = (img.alt || '?').trim().charAt(0).toUpperCase() || '?';
                placeholder.innerHTML = `<span class="img-fallback-letter">${label}</span>`;
                img.replaceWith(placeholder);
            });
        });
    }

    function animateWidgetText(container) {
        const textElements = container.querySelectorAll(
            '.widget-container p, .glass-card p, .canvas-widget p, ' +
            '.widget-container li, .glass-card li, .canvas-widget li'
        );

        textElements.forEach(el => {
            if (el.dataset.typewriterDone === '1') return;
            const fullText = el.textContent || '';
            if (fullText.trim().length < 8) return;
            
            el.dataset.typewriterDone = '1';
            const originalHTML = el.innerHTML;
            
            const words = fullText.split(/(\s+)/);
            el.textContent = '';
            el.classList.add('typewriter-cursor');
            
            let i = 0;
            const speed = 16;
            function step() {
                if (i < words.length) {
                    el.textContent += words[i];
                    i++;
                    setTimeout(step, speed);
                } else {
                    el.innerHTML = originalHTML;
                    el.classList.remove('typewriter-cursor');
                }
            }
            step();
        });
    }

    function renderDynamicComponents(container) {
        reviveScripts(container);
        applyImageFallbacks(container);
        animateWidgetText(container);

        // The welcome message only belongs on an empty canvas.
        const welcome = container.querySelector('#welcome-message');
        if (welcome && container.querySelector('.widget-container, .glass-card, .canvas-widget')) {
            welcome.remove();
        }

        const chartBlocks = container.querySelectorAll('pre code.language-chart');
        chartBlocks.forEach((block) => {
            try {
                const pre = block.parentElement;
                // Already converted on a previous pass — the live canvas sits
                // next to this (hidden) config block; don't double-render.
                if (pre.dataset.chartRendered === '1') return;

                const config = JSON.parse(block.innerText);

                // Ticker comparison charts (trending / compare): every series
                // label is "SYM  +x.x%" (one shared builder makes them all), so
                // that shape — not the widget id, which differs across the
                // fast-path/router/agent lanes — is the gate. A legend click
                // opens that ticker's own stock widget via HN.ask instead of
                // Chart.js's default hide-the-series toggle; shift-click keeps
                // the toggle. Injected here because a function can't ride in
                // the baked JSON config.
                try {
                    const TICKER_PCT = /^([A-Z][A-Z0-9.\-]{0,9})\s+[+\-−]?\d+(?:\.\d+)?%$/;
                    const dsets = (config.data && config.data.datasets) || [];
                    if (dsets.length && dsets.every(d => TICKER_PCT.test(String(d.label || "").trim()))) {
                        const opts = config.options = config.options || {};
                        const plugins = opts.plugins = opts.plugins || {};
                        const legend = plugins.legend = plugins.legend || {};
                        const defToggle = Chart.defaults.plugins.legend.onClick;
                        legend.onClick = function (e, item, legendCtx) {
                            const m = TICKER_PCT.exec(String((item && item.text) || "").trim());
                            if (!m || (e && e.native && e.native.shiftKey)) {
                                defToggle.call(this, e, item, legendCtx);
                                return;
                            }
                            if (window.HN && HN.ask) HN.ask(m[1] + " stock");
                        };
                        legend.onHover = function (e) {
                            const t = e && e.native && e.native.target;
                            if (t) t.style.cursor = "pointer";
                        };
                        legend.onLeave = function (e) {
                            const t = e && e.native && e.native.target;
                            if (t) t.style.cursor = "";
                        };
                    }
                } catch (err) { /* odd config shape → keep the default legend */ }

                // Create canvas container
                const canvasContainer = document.createElement('div');
                canvasContainer.className = 'chart-container';
                canvasContainer.style.position = 'relative';
                canvasContainer.style.width = '100%';
                // Inside a chart widget the canvas fills the body; standalone
                // blocks keep a fixed height.
                if (block.closest('.widget-container')) {
                    canvasContainer.style.height = '100%';
                } else {
                    canvasContainer.style.height = '400px';
                    canvasContainer.style.marginBottom = '1.5rem';
                }

                const canvas = document.createElement('canvas');
                canvasContainer.appendChild(canvas);

                // KEEP the hidden config <pre> and insert the canvas as its
                // sibling. Replacing the pre destroyed the only copy of the
                // chart config, so the client-serialize → server-adopt loop
                // persisted a config-less chart widget: after any reload it was
                // a blank card (nothing left for this converter to find).
                // getCleanedCanvasHtml strips .chart-container + the marker, so
                // the canonical canvas always carries the pristine config block.
                pre.dataset.chartRendered = '1';
                pre.insertAdjacentElement('afterend', canvasContainer);
                
                // Theme-aware defaults: ticks/grid read the active palette so
                // charts stay legible on light themes (egg/pastel) too.
                const cc = window.HN.chartColors();
                Chart.defaults.color = cc.ink;
                Chart.defaults.borderColor = cc.grid;

                const ch = new Chart(canvas, config);
                // Register so a later theme switch recolors + redraws it, and
                // override any colors baked into the config server-side.
                window.HN.registerChart(ch);
                ch.update('none');
            } catch (err) {
                console.error("Failed to render chart component:", err);
            }
        });

        // ─── HOLDER-NETWORK GRAPHS (language-graph → cytoscape) ───
        // Same hydration contract as language-chart above: a hidden
        // <pre><code class="language-graph"> block carries the cytoscape config;
        // we mount an interactive graph as its sibling and KEEP the block so the
        // client-serialize → server-adopt round trip preserves it (getCleaned-
        // CanvasHtml strips the live .graph-canvas + marker, never the config).
        const graphBlocks = container.querySelectorAll('pre code.language-graph');
        graphBlocks.forEach((block) => {
            try {
                const pre = block.parentElement;
                if (pre.dataset.graphRendered === '1') return;
                if (!window.cytoscape) { console.warn('cytoscape not loaded'); return; }
                const cfg = JSON.parse(block.innerText);
                const elements = cfg.elements || [];
                if (!elements.length) return;
                const colors = cfg.colors || {};

                const mount = document.createElement('div');
                mount.className = 'graph-canvas';
                mount.style.position = 'absolute';
                mount.style.inset = '0';
                mount.style.width = '100%';
                mount.style.height = '100%';
                pre.dataset.graphRendered = '1';
                pre.insertAdjacentElement('afterend', mount);

                // Defer to next frame so the mount has real dimensions (cytoscape
                // needs a laid-out container to compute the layout).
                requestAnimationFrame(() => {
                    let cy;
                    try {
                        cy = window.cytoscape({
                            container: mount,
                            elements: elements,
                            style: [
                                { selector: 'node', style: {
                                    'background-color': (n) => colors[n.data('kind')] || '#38bdf8',
                                    'width': 'data(size)', 'height': 'data(size)',
                                    'label': 'data(label)', 'font-size': '7px',
                                    'color': '#cbd5e1', 'text-outline-color': '#0f172a',
                                    'text-outline-width': 1.5, 'text-valign': 'bottom',
                                    'text-margin-y': 2, 'min-zoomed-font-size': 6,
                                    'border-width': 1, 'border-color': 'rgba(255,255,255,0.25)',
                                }},
                                { selector: 'edge', style: {
                                    'width': 'data(width)',
                                    'line-color': 'rgba(148,163,184,0.4)',
                                    'target-arrow-color': 'rgba(148,163,184,0.55)',
                                    'target-arrow-shape': 'triangle',
                                    'arrow-scale': 0.7, 'curve-style': 'bezier',
                                    'opacity': 0.7,
                                }},
                                // Edges touching a red "shared source" pop so the
                                // coordinated-seeding pattern is visible at a glance.
                                { selector: 'edge[?_hot]', style: {
                                    'line-color': 'rgba(239,68,68,0.55)',
                                    'target-arrow-color': 'rgba(239,68,68,0.7)',
                                    'opacity': 0.85,
                                }},
                                { selector: 'node:selected', style: {
                                    'border-width': 3, 'border-color': '#fff',
                                }},
                            ],
                            layout: {
                                name: 'concentric',
                                concentric: (n) => n.data('share') || 0,
                                levelWidth: () => 2,
                                minNodeSpacing: 14,
                                animate: false,
                            },
                            minZoom: 0.2, maxZoom: 3,
                            wheelSensitivity: 0.2,
                        });
                        // Flag edges touching a red "shared source" node so the
                        // coordination pattern stands out (see the [?_hot] style).
                        cy.nodes('[kind = "source"]').connectedEdges().forEach(e => e.data('_hot', 1));
                        // Tap a node → toast its address + share so a whale is
                        // one click from the explorer (and copy the address).
                        cy.on('tap', 'node', (evt) => {
                            const d = evt.target.data();
                            const extra = d.kind === 'source' ? `\n⚠ funded ${d.ties || 2}+ top wallets` : '';
                            const msg = `${d.label || ''} · ${d.share ?? 0}% of supply${extra}\n${d.addr || d.id}`;
                            if (window.HN && window.HN.toast) window.HN.toast(msg);
                            else console.log('[graph]', msg);
                            if (d.addr) navigator.clipboard?.writeText(d.addr).catch(() => {});
                        });
                        // Tap an EDGE → "who sent what to where": amount + count +
                        // direction between the two wallets.
                        cy.on('tap', 'edge', (evt) => {
                            const d = evt.target.data();
                            const src = evt.target.source().data();
                            const tgt = evt.target.target().data();
                            const msg = `${src.label || src.id} → ${tgt.label || tgt.id}\n`
                                + `${d.amount || '?'} tokens over ${d.count || 1} transfer(s)`;
                            if (window.HN && window.HN.toast) window.HN.toast(msg);
                            else console.log('[graph edge]', msg);
                        });
                        cy.fit(undefined, 20);
                    } catch (e) {
                        console.error('cytoscape init failed', e);
                    }
                });
            } catch (err) {
                console.error("Failed to render holder graph:", err);
            }
        });

        // ─── POST-PROCESS WIDGET CONSOLE / CONTROLS ───
        // Covers factory widgets AND LLM-generated glass-cards so every widget
        // on the canvas gets a working close button.
        const widgets = container.querySelectorAll('.widget-container, .glass-card, .canvas-widget');
        widgets.forEach(origWidget => {
            let widget = origWidget;
            const id = widget.id || "";
            
            // 1. Self-heal clock widgets that lost Alpine attributes or are empty
            if (id.includes('clock')) {
                const hasXData = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('clockWidget');
                const hasTime = widget.querySelector('.text-4xl');
                
                if (!hasXData || !hasTime || widget.children.length === 0) {
                    const newWidget = document.createElement('div');
                    newWidget.id = widget.id;
                    newWidget.className = widget.className;
                    newWidget.setAttribute('x-data', "clockWidget('local')");
                    newWidget.innerHTML = `
                        <!-- Close Button -->
                        <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn absolute top-3 right-3 text-white/30 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity">
                            <span class="material-symbols-outlined text-sm">close</span>
                        </button>
                        
                        <div class="flex-grow flex flex-col items-center justify-center mt-2">
                            <div class="text-4xl font-light text-white tracking-widest" x-text="time">--:--:--</div>
                            <div class="text-sm text-slate-400 uppercase tracking-wider mt-1" x-text="date">---</div>
                        </div>
                        
                        <div class="mt-4 opacity-0 group-hover:opacity-100 transition-opacity w-full">
                            <select x-model="selectedTimezone" class="w-full bg-slate-900/50 text-slate-300 text-xs rounded border border-slate-700/50 px-2 py-1.5 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer appearance-none text-center">
                                <option value="local">Local Time</option>
                                <option value="UTC">UTC</option>
                                <option value="America/New_York">New York (EST/EDT)</option>
                                <option value="America/Chicago">Chicago (CST/CDT)</option>
                                <option value="America/Los_Angeles">Los Angeles (PST/PDT)</option>
                                <option value="Europe/London">London (GMT/BST)</option>
                                <option value="Europe/Paris">Paris (CET/CEST)</option>
                                <option value="Asia/Tokyo">Tokyo (JST)</option>
                                <option value="Asia/Shanghai">Shanghai (CST)</option>
                                <option value="Australia/Sydney">Sydney (AEST/AEDT)</option>
                            </select>
                        </div>
                    `;
                    
                    widget.parentNode.replaceChild(newWidget, widget);
                    widget = newWidget;
                }
            }
            
            // 2. Self-heal music player widgets that lost Alpine attributes or are empty
            // NOTE: this innerHTML is a hand-maintained twin of the server
            // template in app/widgets/factory.py (render_mini_music_player).
            // Structural changes there must be mirrored here.
            if ((id.includes('music') || id.includes('player')) && !id.includes('youtube') && !id.includes('video')) {
                const hasXData = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('musicPlayerWidget');
                // Current format passes an options object: musicPlayerWidget({...}).
                // Anything positional — musicPlayerWidget('jazz', ...) or ("jazz", ...)
                // — is a stale node from before the queue/SSE rework: rebuild it.
                const isOldFormat = hasXData && !widget.getAttribute('x-data').includes('musicPlayerWidget({');
                const hasPlayButton = widget.querySelector('.material-symbols-outlined');

                if (!hasXData || isOldFormat || !hasPlayButton || widget.children.length === 0) {
                    let genre = 'jazz';
                    const genreSpan = widget.querySelector('.text-purple-200');
                    if (genreSpan && genreSpan.textContent && genreSpan.textContent.trim() !== 'Radio') {
                        genre = genreSpan.textContent.trim().toLowerCase();
                    }

                    const newWidget = document.createElement('div');
                    newWidget.id = widget.id;
                    // Height moved from a static class to a :class binding so the
                    // queue panel can expand the card — strip any stale static one.
                    newWidget.className = widget.className.replace(/\bh-\[(280|420)px\]\b/g, '').replace(/\s+/g, ' ').trim();
                    newWidget.setAttribute(':class', "showQueue ? 'h-[420px]' : 'h-[280px]'");
                    // kind unknown for a rehydrated node — '' means genre-first
                    // with artist failover. base omitted → hostname:8002 default.
                    newWidget.setAttribute('x-data', `musicPlayerWidget({ genre: ${JSON.stringify(genre)}, kind: "", autoplay: true })`);
                    newWidget.innerHTML = `
                        <!-- Background Blur/Glow effect -->
                        <div class="absolute inset-0 bg-cover bg-center opacity-20 mix-blend-overlay pointer-events-none" style="background-image: url('https://images.unsplash.com/photo-1514525253161-7a46d19cd819?q=80&w=600&auto=format&fit=crop')"></div>
                        <div class="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-900/40 to-transparent pointer-events-none"></div>
                        
                        <!-- Top Bar: Genre / Close -->
                        <div class="relative z-10 flex justify-between items-start">
                            <div class="bg-black/40 backdrop-blur-md px-3 py-1 rounded-full border border-white/10 flex items-center gap-1.5 shadow-sm">
                                <span class="material-symbols-outlined text-[1rem] text-purple-300">graphic_eq</span>
                                <span class="text-xs font-semibold tracking-wider text-purple-200 uppercase" x-text="genreFilter || 'Radio'"></span>
                            </div>
                            <button title="Close Widget" class="close-widget-btn text-white/50 hover:text-white bg-black/20 hover:bg-black/40 rounded-full p-1.5 backdrop-blur-sm transition-all shadow-sm z-20">
                                <span class="material-symbols-outlined text-[1rem]">close</span>
                            </button>
                        </div>
                        
                        <!-- Track Info -->
                        <div class="relative z-10 flex items-center gap-4 mt-2">
                            <div class="w-14 h-14 shrink-0 rounded-2xl bg-gradient-to-tr from-fuchsia-500 to-orange-500 shadow-lg flex items-center justify-center relative overflow-hidden ring-2 ring-white/10">
                                <div class="absolute inset-0 bg-black/20 transition-opacity" :class="{'opacity-0': !isPlaying, 'animate-pulse': isPlaying}"></div>
                                <span class="material-symbols-outlined text-2xl text-white relative z-10">album</span>
                            </div>
                            <div class="flex-grow min-w-0 flex flex-col justify-center" :class="currentTrack ? 'cursor-pointer group/open' : ''" @click="openInFullPlayer()" title="Open in Music Player — keeps playing from here">
                                <h4 class="text-base font-bold text-white truncate leading-tight drop-shadow-md group-hover/open:underline decoration-purple-300/60 underline-offset-2" x-text="currentTrack ? currentTrack.title : 'Searching signals...'"></h4>
                                <p class="text-xs text-purple-200 truncate mt-0.5 drop-shadow-sm font-medium" x-text="currentTrack ? currentTrack.artist : (streamStatus || 'Please wait')"></p>
                            </div>
                        </div>

                        <!-- Queue Panel (toggled by the queue_music button below) -->
                        <div x-show="showQueue" x-transition.opacity class="relative z-10 flex-grow min-h-0 overflow-y-auto rounded-xl bg-black/30 backdrop-blur-md border border-white/10 mt-2 divide-y divide-white/5" style="display: none;">
                            <template x-for="item in upcoming" :key="item.t.id">
                                <div class="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-white/5 cursor-pointer group/row" @click="playAt(item.i)">
                                    <span class="material-symbols-outlined text-[0.9rem] text-purple-300/60 shrink-0">music_note</span>
                                    <div class="min-w-0 flex-grow">
                                        <div class="truncate text-white/90" x-text="item.t.title"></div>
                                        <div class="truncate text-purple-300/70 text-[10px]" x-text="item.t.artist"></div>
                                    </div>
                                    <button @click.stop="removeAt(item.i)" title="Remove from queue" class="opacity-0 group-hover/row:opacity-100 text-white/40 hover:text-red-400 transition-opacity shrink-0">
                                        <span class="material-symbols-outlined text-[0.9rem]">close</span>
                                    </button>
                                </div>
                            </template>
                            <div x-show="!upcoming.length" class="px-3 py-2 text-xs text-white/40">Queue empty — more on the way…</div>
                        </div>

                        <!-- Progress Bar & Time -->
                        <div class="relative z-10 w-full mt-2">
                            <div class="w-full relative group/progress cursor-pointer py-1" @click="handleSeek($event)">
                                <div class="h-1.5 w-full bg-white/10 rounded-full overflow-hidden backdrop-blur-sm shadow-inner relative">
                                    <div class="h-full bg-gradient-to-r from-purple-400 to-fuchsia-400 rounded-full shadow-[0_0_10px_rgba(216,180,254,0.5)] transition-all duration-100" :style="'width: ' + progress + '%'"></div>
                                </div>
                            </div>
                            <div class="flex justify-between text-[10px] text-purple-300 font-mono mt-1 px-0.5">
                                <span x-text="formatTime(currentTime)">0:00</span>
                                <span x-text="formatTime(duration)">0:00</span>
                            </div>
                        </div>
                        
                        <!-- Controls -->
                        <div class="relative z-10 flex items-center justify-between px-1 mt-1">
                            <button @click="toggleShuffle()" class="transition-colors p-1.5 rounded-lg" :class="{'text-purple-300 font-bold bg-white/5': isShuffle, 'text-white/50 hover:text-white': !isShuffle}" title="Shuffle">
                                <span class="material-symbols-outlined text-lg">shuffle</span>
                            </button>
                            
                            <!-- Volume Slider -->
                            <div class="flex items-center gap-1 group/volume">
                                <button @click="toggleMute()" class="text-white/50 hover:text-white transition-colors p-1" title="Mute">
                                    <span class="material-symbols-outlined text-lg" x-text="isMuted ? 'volume_off' : (volume > 0.5 ? 'volume_up' : 'volume_down')">volume_up</span>
                                </button>
                                <input type="range" min="0" max="1" step="0.05" x-model="volume" @input="setVolume(volume)" class="w-10 h-1 bg-white/20 rounded-lg appearance-none cursor-pointer accent-purple-400 group-hover/volume:w-16 transition-all duration-200">
                            </div>
                            
                            <div class="flex items-center gap-2">
                                <button @click="prevTrack()" class="w-8 h-8 rounded-full bg-white/5 hover:bg-white/10 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                                    <span class="material-symbols-outlined text-base">skip_previous</span>
                                </button>
                                
                                <button @click="playPause()" class="w-10 h-10 rounded-2xl bg-purple-300 hover:bg-purple-200 text-slate-900 flex items-center justify-center shadow-lg transition-all active:scale-95" :disabled="!currentTrack">
                                    <span class="material-symbols-outlined text-xl" x-text="isPlaying ? 'pause' : 'play_arrow'" style="font-variation-settings: 'FILL' 1;">play_arrow</span>
                                </button>
                                
                                <button @click="nextTrack()" class="w-8 h-8 rounded-full bg-white/5 hover:bg-white/10 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                                    <span class="material-symbols-outlined text-base">skip_next</span>
                                </button>
                            </div>
                            
                            <button @click="toggleRepeat()" class="transition-colors p-1.5 rounded-lg" :class="{'text-purple-300 font-bold bg-white/5': isRepeat, 'text-white/50 hover:text-white': !isRepeat}" title="Repeat">
                                <span class="material-symbols-outlined text-lg">repeat</span>
                            </button>

                            <button @click="showQueue = !showQueue" class="transition-colors p-1.5 rounded-lg" :class="{'text-purple-300 font-bold bg-white/5': showQueue, 'text-white/50 hover:text-white': !showQueue}" title="Queue">
                                <span class="material-symbols-outlined text-lg">queue_music</span>
                            </button>
                        </div>

                        <div x-show="error" x-transition class="absolute bottom-2 left-1/2 -translate-x-1/2 bg-red-500/90 text-white text-xs px-3 py-1 rounded-full backdrop-blur-md whitespace-nowrap shadow-lg z-20" x-text="error" style="display: none;"></div>
                    `;
                    
                    widget.parentNode.replaceChild(newWidget, widget);
                    widget = newWidget;
                }
            }
            
            // 3. Self-heal youtube player widgets that lost Alpine attributes or are empty
            if (id.includes('youtube') || id.includes('video')) {
                const hasXData = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('youtubePlayerWidget');
                const isOldFormat = hasXData && widget.getAttribute('x-data').includes("youtubePlayerWidget('");
                
                if (!hasXData || isOldFormat || widget.children.length === 0) {
                    let videoId = '';
                    let title = 'YouTube Player';
                    
                    const xdata = widget.getAttribute('x-data') || '';
                    const xdataMatch = xdata.match(/youtubePlayerWidget\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"](.*)['"]\s*\)/);
                    if (xdataMatch) {
                        videoId = xdataMatch[1];
                        title = xdataMatch[2];
                    }
                    
                    if (!videoId) {
                        const iframe = widget.querySelector('iframe');
                        if (iframe && iframe.src) {
                            const match = iframe.src.match(/\/embed\/([a-zA-Z0-9_-]{11})/);
                            if (match) {
                                videoId = match[1];
                            }
                        }
                    }
                    
                    const titleHeader = widget.querySelector('h3');
                    if (titleHeader && titleHeader.textContent && (!title || title === 'YouTube Player')) {
                        title = titleHeader.textContent.trim();
                    }
                    
                    const newWidget = document.createElement('div');
                    newWidget.id = widget.id;
                    newWidget.className = widget.className;
                    newWidget.setAttribute('x-data', `youtubePlayerWidget(${JSON.stringify(videoId)}, ${JSON.stringify(title)})`);
                    newWidget.innerHTML = `
                        <!-- Title Bar -->
                        <div class="flex items-center justify-between bg-black/30 p-3 border-b border-white/10 relative z-20">
                            <div class="flex items-center gap-2">
                                <span class="text-xl text-red-500">📺</span>
                                <h3 class="font-bold text-white tracking-wide truncate max-w-[250px]" x-text="title"></h3>
                                <span x-show="isLoading" class="text-xs text-slate-400 italic animate-pulse">Resolving stream...</span>
                            </div>
                            <button title="Close Widget" @click="window.WidgetManager.dismiss($el.closest('.widget-container'))" class="close-widget-btn text-white/50 hover:text-red-400 transition-colors">
                                <span class="material-symbols-outlined text-[1.2rem]">close</span>
                            </button>
                        </div>
                        <!-- Video Embed -->
                        <div class="w-full flex-grow bg-black relative flex items-center justify-center">
                            <!-- Loading state overlay -->
                            <div x-show="isLoading" class="absolute inset-0 bg-slate-950/80 flex flex-col items-center justify-center z-10">
                                <span class="material-symbols-outlined text-4xl text-purple-400 animate-spin mb-2">sync</span>
                                <span class="text-sm text-slate-300">Searching YouTube...</span>
                            </div>
                            <!-- Error state overlay -->
                            <div x-show="error" class="absolute inset-0 bg-slate-950/90 flex flex-col items-center justify-center p-4 text-center z-10 gap-2">
                                <span class="material-symbols-outlined text-4xl text-red-500 mb-2">error</span>
                                <span class="text-sm text-slate-200" x-text="error"></span>
                                <a x-show="watchUrl" :href="watchUrl" target="_blank" rel="noopener" class="text-sm text-purple-300 underline hover:text-purple-200">Watch on YouTube ↗</a>
                            </div>
                            <!-- Iframe player -->
                            <template x-if="embedUrl">
                                <iframe :src="embedUrl" class="w-full h-full border-none" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
                            </template>
                        </div>
                    `;
                    widget.parentNode.replaceChild(newWidget, widget);
                    widget = newWidget;
                }
            }
            
            // 4. Self-heal checklist widgets that lost Alpine attributes or are empty
            if (id.includes('checklist') || id.includes('todo')) {
                const hasXData = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('checklistWidget');
                const isOldFormat = hasXData && widget.getAttribute('x-data').includes("checklistWidget('");
                
                if (!hasXData || isOldFormat || widget.children.length === 0) {
                    let title = 'Checklist';
                    const header = widget.querySelector('h3');
                    if (header && header.textContent) {
                        title = header.textContent.trim();
                    }
                    
                    const listItems = [];
                    widget.querySelectorAll('li').forEach(li => {
                        if (li.textContent.includes('No tasks yet')) return;
                        const span = li.querySelector('span');
                        const text = span ? span.textContent.trim() : li.textContent.replace('×', '').trim();
                        const checkbox = li.querySelector('input[type="checkbox"]');
                        const done = checkbox ? checkbox.checked : li.classList.contains('line-through') || (span && span.classList.contains('line-through'));
                        if (text) {
                            listItems.push({ text, done });
                        }
                    });
                    
                    const itemsJson = JSON.stringify(listItems).replace(/"/g, '&quot;');
                    
                    const newWidget = document.createElement('div');
                    newWidget.id = widget.id;
                    newWidget.className = widget.className;
                    newWidget.setAttribute('x-data', `checklistWidget(${JSON.stringify(title)}, ${itemsJson})`);
                    newWidget.innerHTML = `
                        <!-- Close Button -->
                        <button title="Close Widget" class="close-widget-btn absolute top-4 right-4 text-white/40 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity z-20">
                            <span class="material-symbols-outlined text-[1.2rem]">close</span>
                        </button>
                        
                        <h3 class="text-lg font-bold mb-3 text-white truncate pr-6" x-text="title"></h3>
                        <div class="flex gap-2 mb-3">
                            <input x-model="newItem" @keydown.enter="addTask" type="text" placeholder="Add task..." class="px-3 py-1.5 rounded-xl bg-white/5 text-white flex-grow border border-white/10 focus:outline-none focus:border-purple-500 text-sm">
                            <button @click="addTask" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 transition-colors rounded-xl text-white font-bold shadow">+</button>
                        </div>
                        <ul class="space-y-2 overflow-y-auto flex-grow pr-1 custom-scrollbar">
                            <template x-for="(item, idx) in items" :key="idx">
                                <li class="flex items-center gap-3 p-2 rounded-xl transition-all duration-300 group/item border border-transparent"
                                    :class="{'bg-green-500/10 border-green-500/20 text-green-300': item.done, 'hover:bg-white/5': !item.done}">
                                    <input type="checkbox" x-model="item.done" class="rounded border-white/10 text-purple-600 focus:ring-purple-500 w-4 h-4 cursor-pointer">
                                    <span :class="{'line-through opacity-50': item.done}" x-text="item.text" class="text-sm flex-grow cursor-pointer" @click="item.done = !item.done"></span>
                                    <button @click="removeTask(idx)" class="opacity-0 group-hover/item:opacity-100 text-red-400 hover:text-red-300 transition-opacity">×</button>
                                </li>
                            </template>
                            <li x-show="items.length === 0" class="text-slate-400 text-xs italic text-center py-4">No tasks yet</li>
                        </ul>
                    `;
                    widget.parentNode.replaceChild(newWidget, widget);
                    widget = newWidget;
                }
            }
            
            // 3. Ensure close button exists and attach vanilla fallback click listener
            let closeBtn = widget.querySelector('.close-widget-btn') || widget.querySelector('button[title="Close Widget"]');
            if (!closeBtn) {
                widget.classList.add('group');
                
                closeBtn = document.createElement('button');
                closeBtn.className = 'close-widget-btn absolute top-3 right-3 text-white/30 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity';
                closeBtn.setAttribute('title', 'Close Widget');
                closeBtn.innerHTML = '<span class="material-symbols-outlined text-sm">close</span>';
                
                if (widget.style.position !== 'absolute' && getComputedStyle(widget).position === 'static') {
                    widget.style.position = 'relative';
                }
                widget.appendChild(closeBtn);
            }
            
            if (closeBtn && !closeBtn._hasListener) {
                closeBtn._hasListener = true;
                closeBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    if (window.WidgetManager) {
                        window.WidgetManager.dismiss(widget);
                    } else {
                        widget.remove();
                    }
                });
            }
            
            // 2. Ensure clock widget has timezone selector if it's a clock and handles timezone updates
            const isClock = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('clockWidget');
            if (isClock) {
                let select = widget.querySelector('select');
                if (!select) {
                    const selectContainer = document.createElement('div');
                    selectContainer.className = 'mt-4 opacity-0 group-hover:opacity-100 transition-opacity w-full';
                    selectContainer.innerHTML = `
                        <select class="w-full bg-slate-900/50 text-slate-300 text-xs rounded border border-slate-700/50 px-2 py-1.5 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer appearance-none text-center">
                            <option value="local">Local Time</option>
                            <option value="UTC">UTC</option>
                            <option value="America/New_York">New York (EST/EDT)</option>
                            <option value="America/Chicago">Chicago (CST/CDT)</option>
                            <option value="America/Los_Angeles">Los Angeles (PST/PDT)</option>
                            <option value="Europe/London">London (GMT/BST)</option>
                            <option value="Europe/Paris">Paris (CET/CEST)</option>
                            <option value="Asia/Tokyo">Tokyo (JST)</option>
                            <option value="Asia/Shanghai">Shanghai (CST)</option>
                            <option value="Australia/Sydney">Sydney (AEST/AEDT)</option>
                        </select>
                    `;
                    widget.appendChild(selectContainer);
                    select = selectContainer.querySelector('select');
                }
                
                if (select && !select._hasListener) {
                    const updateSelectFromAlpine = () => {
                        if (window.Alpine) {
                            try {
                                const alpineData = window.Alpine.$data(widget);
                                if (alpineData) {
                                    select.value = alpineData.selectedTimezone || 'local';
                                    if (!select._hasListener) {
                                        select._hasListener = true;
                                        select.addEventListener('change', (e) => {
                                            alpineData.selectedTimezone = e.target.value;
                                            if (typeof alpineData.updateTime === 'function') {
                                                alpineData.updateTime();
                                            }
                                        });
                                    }
                                    return true;
                                }
                            } catch (err) {
                                console.warn("Failed to get Alpine data for clock widget:", err);
                            }
                        }
                        return false;
                    };
                    
                    if (!updateSelectFromAlpine()) {
                        let retries = 0;
                        const timer = setInterval(() => {
                            retries++;
                            if (updateSelectFromAlpine() || retries > 20) {
                                clearInterval(timer);
                            }
                        }, 200);
                    }
                }
            }
        });

        // Clean up any dynamic sibling elements generated by templates to avoid duplication
        const youtubeIframes = container.querySelectorAll('[x-data*="youtubePlayerWidget"] iframe');
        youtubeIframes.forEach(iframe => {
            const widget = iframe.closest('.widget-container');
            if (widget && (widget.__x || widget._x_dataStack)) {
                return;
            }
            iframe.remove();
        });
        
        const checklistItems = container.querySelectorAll('[x-data*="checklistWidget"] ul li');
        checklistItems.forEach(li => {
            if (!li.getAttribute('x-show') && !li.classList.contains('close-widget-btn')) {
                const widget = li.closest('.widget-container');
                if (widget && (widget.__x || widget._x_dataStack)) {
                    return;
                }
                li.remove();
            }
        });

        // Force Alpine to initialize any uninitialized nodes inside container
        if (window.Alpine && typeof window.Alpine.initTree === 'function') {
            try {
                window.Alpine.initTree(container);
            } catch (err) {
                console.warn("Failed to execute Alpine.initTree:", err);
            }
        }
        initCanvasWidgets(container);
    }

    // Widgets that have already played their power-on animation, so a re-render
    // (every SSE component event replaces the canvas wholesale) doesn't replay it.
    const poweredOnWidgets = new Set();

    /**
     * Lay the canvas out and power on anything new.
     *
     * This replaced a hand-rolled absolute-positioning tiler that scanned for a
     * free rectangle per widget. It only advanced its cursor for widgets it
     * placed itself, so once two widgets carried saved coordinates the cursor
     * stayed at the origin, and the third widget's collision scan walked it off
     * the bottom of the grid's fixed 800px min-height — it rendered, just where
     * nobody could see it. CSS grid handles flow, so the layout math is gone.
     */
    function initCanvasWidgets(container) {
        const grid = container.querySelector('#dashboard-grid');
        if (!grid) return;

        const items = grid.querySelectorAll('.widget-container, .glass-card, .canvas-element, .rendered-component');

        items.forEach(item => {
            if (!item.id) {
                item.id = 'item-' + Math.random().toString(36).substr(2, 9);
            }

            // Canvases persisted by the old tiler still carry inline coordinates.
            // Left in place they'd yank widgets out of grid flow and stack them
            // on top of each other, so strip them on the way in.
            item.style.removeProperty('position');
            item.style.removeProperty('left');
            item.style.removeProperty('top');
            item.style.removeProperty('width');
            item.style.removeProperty('height');
            item.style.removeProperty('z-index');
            item.removeAttribute('draggable');

            if (!poweredOnWidgets.has(item.id)) {
                poweredOnWidgets.add(item.id);
                item.classList.add('crt-on');
                // crt-on starts at opacity 0 / scaleX 0 with fill-mode "both", so a
                // widget whose animation never runs to completion stays INVISIBLE.
                // animationend can be missed if the canvas is re-painted mid-flight,
                // so the timeout guarantees the class comes off either way.
                const settle = () => item.classList.remove('crt-on');
                item.addEventListener('animationend', settle, { once: true });
                setTimeout(settle, 900);
            }
        });

        grid.style.removeProperty('min-height');
    }


    // ─── UTILS ─────────────────────────────────────────────────
    async function fetchModels() {
        if (!elements.modelSelect) return;
        try {
            const res = await fetch("/models");
            if (res.ok) {
                const data = await res.json();
                elements.modelSelect.innerHTML = "";
                
                data.models.forEach(m => {
                    const option = document.createElement("option");
                    option.value = JSON.stringify({ provider: m.provider, model: m.model });
                    option.textContent = m.label;
                    elements.modelSelect.appendChild(option);
                });
                
                if (data.models.length > 0) {
                    let defaultIndex = 0;
                    const options = Array.from(elements.modelSelect.options);
                    const vllm2Index = options.findIndex(opt => {
                        try {
                            const val = JSON.parse(opt.value);
                            return val.provider === "vllm-2";
                        } catch(e) {
                            return false;
                        }
                    });
                    const vllmIndex = options.findIndex(opt => {
                        try {
                            const val = JSON.parse(opt.value);
                            return val.provider === "vllm";
                        } catch(e) {
                            return false;
                        }
                    });
                    if (vllm2Index !== -1) {
                        defaultIndex = vllm2Index;
                    } else if (vllmIndex !== -1) {
                        defaultIndex = vllmIndex;
                    }
                    elements.modelSelect.options[defaultIndex].selected = true;
                }
            } else {
                elements.modelSelect.innerHTML = '<option value="">Failed to load models</option>';
            }
        } catch (e) {
            console.error("Failed to load models", e);
            elements.modelSelect.innerHTML = '<option value="">Failed to load models</option>';
        }
    }


    async function checkHealth() {
        try {
            const res = await fetch("/health/model");
            if (res.ok) {
                elements.healthIndicator.classList.remove("offline");
                elements.healthIndicator.classList.add("online");
            } else {
                elements.healthIndicator.classList.remove("online");
                elements.healthIndicator.classList.add("offline");
            }
        } catch (e) {
            elements.healthIndicator.classList.remove("online");
            elements.healthIndicator.classList.add("offline");
        }
    }

    function generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    // ─── TTS & CHAT HISTORY UTILS ─────────────────────────────
    let sentenceBuffer = "";
    const ttsQueue = [];
    let isProcessingQueue = false;
    let currentAudio = null;
    const ttsAudioCache = new Map();
    let consecutiveTtsFailures = 0;
    const MAX_TTS_FAILURES = 3;
    // After MAX_TTS_FAILURES the service is presumed offline, but only for a
    // cooldown window — otherwise a transient outage kills TTS for the whole
    // session with no way back short of a page reload (the old latch never
    // reset, which is why toggling mute "did nothing"). This self-heals.
    const TTS_COOLDOWN_MS = 30000;
    let ttsDisabledUntil = 0;
    // Browsers block Audio.play() until the page has had a user gesture. We prime
    // on the first interaction so TTS triggered off the SSE stream (or the wake
    // word) isn't silently rejected by autoplay policy.
    let audioUnlocked = false;
    let sharedAudioCtx = null;

    function ttsAvailable() {
        return !state.isMuted && Date.now() >= ttsDisabledUntil;
    }

    function markTtsHealthy() {
        consecutiveTtsFailures = 0;
        ttsDisabledUntil = 0;
        setTtsIndicator(false);
    }

    function markTtsOffline() {
        ttsDisabledUntil = Date.now() + TTS_COOLDOWN_MS;
        setTtsIndicator(true);
    }

    function setTtsIndicator(offline) {
        if (!elements.btnMute) return;
        const showOffline = offline && !state.isMuted;
        elements.btnMute.classList.toggle("tts-offline", showOffline);
        if (showOffline) {
            elements.btnMute.title = "Voice unavailable (TTS offline) — retrying…";
        } else {
            updateMuteButtonUI();
        }
    }

    function primeAudioPlayback() {
        if (audioUnlocked) return;
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (Ctx) {
                sharedAudioCtx = sharedAudioCtx || new Ctx();
                if (sharedAudioCtx.state === "suspended") sharedAudioCtx.resume();
            }
        } catch (e) {}
        audioUnlocked = true;
    }

    function updateMuteButtonUI() {
        if (!elements.btnMute) return;
        if (state.isMuted) {
            elements.btnMute.classList.add("muted");
            elements.btnMute.title = "Unmute Voice";
            elements.btnMute.innerHTML = `
                <svg class="mute-icon" viewBox="0 0 24 24">
                    <path fill="currentColor" d="M12,4L9.91,6.09L12,8.18M4.27,3L3,4.27L7.73,9H3V15H7L12,20V13.27L16.25,17.52C15.58,18.04 14.83,18.45 14,18.7V20.76C15.38,20.45 16.63,19.78 17.68,18.9L20.73,21.95L22,20.68M19,12C19,12.94 18.8,13.82 18.46,14.64L19.97,16.15C20.63,14.91 21,13.5 21,12C21,7.72 18,4.14 14,3.23V5.29C16.89,6.15 19,8.83 19,12M16.5,12C16.5,10.23 15.5,8.71 14,7.97V10.18L16.45,12.63C16.48,12.43 16.5,12.22 16.5,12Z"/>
                </svg>
            `;
        } else {
            elements.btnMute.classList.remove("muted");
            elements.btnMute.title = "Mute Voice";
            elements.btnMute.innerHTML = `
                <svg class="mute-icon" viewBox="0 0 24 24">
                    <path fill="currentColor" d="M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.85 14,18.71V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.77 16.5,12M3,9V15H7L12,20V4L7,9H3Z"/>
                </svg>
            `;
        }
    }

    function appendChatMessageToHistory(role, content, canvasUpdated = false) {
        if (!elements.chatHistoryMessages) return;

        const messageDiv = document.createElement("div");
        messageDiv.className = `chat-message ${role}`;

        if (role === "user") {
            messageDiv.textContent = content;
        } else {
            messageDiv.innerHTML = formatAssistantChatBubble(content, canvasUpdated);
        }
        
        elements.chatHistoryMessages.appendChild(messageDiv);
        
        // Auto-expand on new message
        if (elements.chatHistoryMessages.style.display === "none") {
            elements.chatHistoryMessages.style.display = "flex";
            if (elements.btnToggleHistory) elements.btnToggleHistory.innerText = "▼";
        }
        
        elements.chatHistoryMessages.scrollTop = elements.chatHistoryMessages.scrollHeight;
    }

    // A bubble that grows as the tokens arrive, instead of appearing whole when
    // the turn ends. The chat pane used to accumulate every chunk into `fullText`
    // and render NOTHING until `done`, while the activity log ticked on every
    // event — so a conversational turn looked frozen for its whole duration and a
    // 4-second one looked identical to a 90-second one.
    //
    // Ceiling worth knowing: on widget turns the server cuts the agent stream once
    // the canvas settles and sends ONE synthesized sentence, so those legitimately
    // still arrive in a single piece. This is for the turns that genuinely stream.
    function createLiveAssistantBubble() {
        let div = null, pending = false, latest = "";

        // Re-formatting runs the text through DOMPurify, so it is coalesced to one
        // repaint per frame rather than one per token.
        function repaint(canvasUpdated) {
            if (!div) return;
            div.innerHTML = formatAssistantChatBubble(latest, canvasUpdated);
            elements.chatHistoryMessages.scrollTop = elements.chatHistoryMessages.scrollHeight;
        }

        return {
            started: () => Boolean(div),
            append(text) {
                latest = text;
                if (!elements.chatHistoryMessages) return;
                if (!div) {
                    div = document.createElement("div");
                    div.className = "chat-message assistant is-streaming";
                    elements.chatHistoryMessages.appendChild(div);
                    if (elements.chatHistoryMessages.style.display === "none") {
                        elements.chatHistoryMessages.style.display = "flex";
                        if (elements.btnToggleHistory) elements.btnToggleHistory.innerText = "▼";
                    }
                }
                if (pending) return;
                pending = true;
                requestAnimationFrame(() => { pending = false; repaint(false); });
            },
            // Finalize the bubble already on screen rather than appending a second
            // one — `canvasUpdated` is only known at the end of the turn.
            finalize(text, canvasUpdated) {
                if (!div) return false;
                latest = text;
                div.classList.remove("is-streaming");
                repaint(canvasUpdated);
                return true;
            }
        };
    }

    function formatAssistantChatBubble(content, canvasUpdated = false) {
        // Canvas HTML never belongs in the chat bubble — it renders on the canvas.
        content = (content || "").replace(/<!--CANVAS_HTML_START-->[\s\S]*?<!--CANVAS_HTML_END-->/g, "");
        let temp = document.createElement("div");
        temp.innerHTML = content;

        let components = temp.querySelectorAll(".widget-container, .glass-card, .canvas-element, .rendered-component, .chart-container, .dashboard-grid, .system-message, style, script");
        let hasComponent = components.length > 0 || canvasUpdated;

        components.forEach(el => el.remove());
        let cleaned = temp.innerHTML;
        
        let htmlText = "";
        if (cleaned.trim()) {
            // 'target'/'rel' must be allowed here too. DOMPurify's default allowlist
            // covers href and rel but NOT target, so without this a link in chat
            // history kept its href and navigated the whole app away in-tab —
            // losing the canvas — while the same link on the canvas opened a new
            // tab. Matches CANVAS_DOMPURIFY_CONFIG.
            htmlText = DOMPurify.sanitize(marked.parse(cleaned), {
                ADD_ATTR: ['style', 'class', 'target', 'rel'],
                FORCE_BODY: true
            });
            // marked emits <a href> with no target, so chat links navigated the
            // whole app away in-tab and the canvas was lost. Applied AFTER
            // sanitize so we're decorating markup DOMPurify has already cleared,
            // never smuggling attributes past it.
            const linkFix = document.createElement('div');
            linkFix.innerHTML = htmlText;
            linkFix.querySelectorAll('a[href]').forEach(a => {
                a.setAttribute('target', '_blank');
                a.setAttribute('rel', 'noopener noreferrer');
            });
            htmlText = linkFix.innerHTML;
        }

        if (hasComponent) {
            htmlText += `<div class="chat-component-placeholder">🎨 Generated visual component on canvas</div>`;
            return htmlText;
        }
        
        return htmlText || `<div class="chat-message-empty text-white/30 text-xs italic">No text response</div>`;
    }

    function handleIncomingChunk(textToken) {
        sentenceBuffer += textToken;
        let match;
        const sentenceRegex = /[^.!?]+[.!?]+(?=\s|$)/g;
        
        while ((match = sentenceRegex.exec(sentenceBuffer)) !== null) {
            const sentence = match[0].trim();
            if (sentence) {
                enqueueTTS(sentence);
            }
            sentenceBuffer = sentenceBuffer.substring(match.index + match[0].length);
            sentenceRegex.lastIndex = 0;
        }
    }

    function flushSentenceBuffer() {
        const remaining = sentenceBuffer.trim();
        if (remaining) {
            enqueueTTS(remaining);
        }
        sentenceBuffer = "";
    }

    function enqueueTTS(sentence) {
        const cleanText = cleanTextForTTS(sentence);
        if (!cleanText) return;
        // Claim the pending association: this sentence owns it, and later
        // sentences in the same turn must not inherit it.
        const widgetId = pendingSentenceWidgetId;
        pendingSentenceWidgetId = null;
        // Speak the answer, never the process. Dropped here rather than upstream
        // because sentences are only assembled at this point — the server streams
        // tokens, so it cannot tell where a narration sentence starts or ends.
        // The text still appears in the chat bubble; it is only not READ ALOUD.
        if (isNarrationSentence(cleanText)) {
            console.debug("[TTS] skipped narration:", cleanText.slice(0, 80));
            return;
        }

        if (ttsAudioCache.has(cleanText)) {
            const cached = ttsAudioCache.get(cleanText);
            const item = {
                text: sentence,
                cleanText: cleanText,
                widgetId: widgetId,
                audioUrl: cached.audioUrl,
                audio: new Audio(cached.audioUrl),
                status: 'ready',
                fetchPromise: null
            };
            ttsQueue.push(item);
            processTTSQueue();
            return;
        }

        const item = {
            text: sentence,
            cleanText: cleanText,
            widgetId: widgetId,
            audioUrl: null,
            audio: null,
            status: 'pending',
            fetchPromise: null
        };

        // Start background fetch immediately unless muted or TTS is in its
        // offline cooldown (see ttsAvailable — the cooldown self-heals).
        if (ttsAvailable()) {
            item.status = 'fetching';
            item.fetchPromise = (async () => {
                try {
                    const res = await fetch("/tts/synthesize", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ text: cleanText })
                    });
                    if (!res.ok) throw new Error("TTS proxy error");
                    const blob = await res.blob();
                    item.audioUrl = URL.createObjectURL(blob);
                    item.audio = new Audio(item.audioUrl);
                    item.status = 'ready';
                    markTtsHealthy();
                    ttsAudioCache.set(cleanText, { audioUrl: item.audioUrl });
                } catch (e) {
                    console.error("Background TTS fetch failed:", e);
                    item.status = 'error';
                    consecutiveTtsFailures++;
                    if (consecutiveTtsFailures >= MAX_TTS_FAILURES) {
                        console.warn(`TTS service appears offline. Pausing TTS ${TTS_COOLDOWN_MS/1000}s, then auto-retrying.`);
                        markTtsOffline();
                    }
                }
            })();
        }

        ttsQueue.push(item);
        processTTSQueue();
    }

    function clearSpeechQueue() {
        ttsQueue.forEach(item => {
            if (item.audio) {
                try {
                    item.audio.pause();
                } catch(e) {}
            }
            if (item.audioUrl) {
                try {
                    URL.revokeObjectURL(item.audioUrl);
                } catch(e) {}
            }
        });
        ttsQueue.length = 0;
        // Speech was cancelled (muted, interrupted, or a new turn started). Nothing
        // will cue the held widgets now, so show them immediately — muting the
        // assistant must never mean losing content.
        revealAllPending();
        if (currentAudio) {
            try {
                currentAudio.pause();
            } catch(e) {}
            currentAudio = null;
        }
        const overlay = document.getElementById("speech-overlay");
        if (overlay) {
            overlay.style.display = "none";
            overlay.innerHTML = "";
            overlay.classList.remove("sentence-fade-out");
        }
        isProcessingQueue = false;
        sentenceBuffer = "";
    }

    // The model's PROCESS commentary, which must never be read aloud. Observed
    // spoken verbatim on a live "whats the news" turn: "The Google RSS URLs can't
    // be fetched directly — they're RSS feeds, not regular pages. I'll search for
    // the actual article URLs from the source outlets instead." That is the model
    // narrating its own plumbing to someone who asked for the headlines.
    //
    // Mirrors _NARRATION_SENTENCE_RE / _TOOL_TALK_RE in app/main.py, which already
    // strip this — but only for the no-widget fallback CARD, never for speech.
    // Matched per sentence, because narration usually arrives as its own sentence
    // alongside a real answer.
    const TTS_NARRATION_RE =
        /^\s*(?:(?:now|next|first|then|so|okay|ok|alright|great|perfect)[,!.]?\s+)?(?:i\s*(?:'ll|'ve|'m)?\s*(?:have|will|am|can|need|should|shall|going\s+to|now\s+have)?|let\s+me|let's)\b/i;
    const TTS_TOOL_TALK_RE =
        /\b(?:data_card|canvas_add_widget|canvas_modify_dom|canvas_read_dom|widget_type|stock_card|scoreboard|a\s+widget|the\s+canvas|to\s+your\s+canvas)\b/i;

    function isNarrationSentence(text) {
        const t = (text || "").trim();
        if (!t) return true;
        return TTS_NARRATION_RE.test(t) || TTS_TOOL_TALK_RE.test(t);
    }

    function cleanTextForTTS(text) {
        let cleaned = text.replace(/<[^>]*>/g, "");
        cleaned = cleaned.replace(/[\*_#`~]/g, "");
        cleaned = cleaned.replace(/\[([^\]]+)\]\([^\)]+\)/g, "$1");
        // A URL read aloud is unusable noise and buries the sentence around it.
        cleaned = cleaned.replace(/https?:\/\/\S+/g, "");
        cleaned = cleaned.replace(/\s+/g, " ").trim();
        return cleaned;
    }

    async function processTTSQueue() {
        if (isProcessingQueue) return;
        if (ttsQueue.length === 0) {
            hideSpeechOverlay();
            // Nothing left to say, so nothing is coming to cue the rest.
            revealAllPending();
            return;
        }
        
        isProcessingQueue = true;
        const item = ttsQueue.shift();
        
        try {
            await playSentenceTTS(item);
        } catch (err) {
            console.error("Error playing sentence TTS:", err);
        } finally {
            isProcessingQueue = false;
            setTimeout(processTTSQueue, 50);
        }
    }

    function playSentenceTTS(item) {
        return new Promise(async (resolve) => {
            const overlay = document.getElementById("speech-overlay");
            const cleanText = item.cleanText;
            const words = item.text.split(/\s+/).filter(w => w.length > 0);
            
            overlay.innerHTML = "";
            overlay.style.display = "block";
            overlay.classList.remove("sentence-fade-out");
            // This sentence is starting — bring in the widget it describes, so the
            // canvas fills at the pace of the voice rather than all at once.
            // Prefer the widget the SERVER paired with this sentence; fall back to
            // queue order only when there is no pairing (free agent prose).
            if (item.widgetId) revealWidget(item.widgetId); else revealNextWidget();
            
            words.forEach((word, index) => {
                const span = document.createElement("span");
                span.className = "word";
                span.textContent = word;
                span.style.animationDelay = `${index * 0.08}s`;
                overlay.appendChild(span);
            });

            const fadeAndFinish = () => {
                overlay.classList.add("sentence-fade-out");
                setTimeout(() => {
                    overlay.style.display = "none";
                    overlay.innerHTML = "";
                    resolve();
                }, 400);
            };

            if (state.isMuted) {
                const simulationDuration = Math.max(1500, words.length * 300);
                setTimeout(fadeAndFinish, simulationDuration);
            } else {
                // Await background fetch to complete if it's still fetching
                if (item.fetchPromise) {
                    await item.fetchPromise;
                }

                if (item.status === 'ready' && item.audio) {
                    primeAudioPlayback();
                    currentAudio = item.audio;
                    
                    item.audio.onended = () => {
                        URL.revokeObjectURL(item.audioUrl);
                        if (currentAudio === item.audio) {
                            currentAudio = null;
                        }
                        fadeAndFinish();
                    };
                    
                    item.audio.onerror = (err) => {
                        console.error("Audio playback error:", err);
                        URL.revokeObjectURL(item.audioUrl);
                        if (currentAudio === item.audio) {
                            currentAudio = null;
                        }
                        const fallbackDuration = Math.max(1500, words.length * 300);
                        setTimeout(fadeAndFinish, fallbackDuration);
                    };
                    
                    item.audio.play().catch(err => {
                        console.error("Audio play failed:", err);
                        URL.revokeObjectURL(item.audioUrl);
                        if (currentAudio === item.audio) {
                            currentAudio = null;
                        }
                        const fallbackDuration = Math.max(1500, words.length * 300);
                        setTimeout(fadeAndFinish, fallbackDuration);
                    });
                } else {
                    // Fallback to silent simulation if fetch failed
                    console.warn("Background fetch failed or was not initialized, falling back to silent visualization.");
                    const fallbackDuration = Math.max(1500, words.length * 300);
                    setTimeout(fadeAndFinish, fallbackDuration);
                }
            }
        });
    }

    function hideSpeechOverlay() {
        const overlay = document.getElementById("speech-overlay");
        if (overlay && overlay.style.display !== "none") {
            overlay.classList.add("sentence-fade-out");
            setTimeout(() => {
                overlay.style.display = "none";
                overlay.innerHTML = "";
            }, 400);
        }
    }
});
