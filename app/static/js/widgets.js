// Global Alpine.js widget registry for the Smart Dashboard Lego System

// Loads the YouTube IFrame API once. It is the only way to detect
// "Video unavailable" / embed-blocked errors (codes 100/101/150) so the
// player widget can auto-advance to an embeddable alternative.
function loadYouTubeIframeApi() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    if (window._ytApiPromise) return window._ytApiPromise;
    window._ytApiPromise = new Promise(resolve => {
        const prev = window.onYouTubeIframeAPIReady;
        window.onYouTubeIframeAPIReady = () => {
            if (typeof prev === 'function') prev();
            resolve();
        };
        const tag = document.createElement('script');
        tag.src = 'https://www.youtube.com/iframe_api';
        document.head.appendChild(tag);
    });
    return window._ytApiPromise;
}

document.addEventListener('alpine:init', () => {
    
    // 1. Checklist Widget
    // Rich ticker widget. The snapshot is baked in server-side so it paints
    // immediately; switching range refetches /api/stock directly rather than
    // running another agentic turn (which would cost ~a minute per tab click).
    Alpine.data('stockCardWidget', (initial) => ({
        snapshot: initial || {},
        ranges: ['1d', '5d', '1mo', '3mo', '6mo', '1y', '5y', '10y', 'max'],
        loading: false,
        chart: null,

        init() {
            this.$nextTick(() => this.drawChart());
            // Redraw the price sparkline when the palette changes — its axis
            // colors are theme-derived and Chart.js can't read CSS vars live.
            this._onTheme = () => this.$nextTick(() => this.drawChart());
            window.addEventListener('hn:theme', this._onTheme);
        },

        destroy() {
            if (this._onTheme) window.removeEventListener('hn:theme', this._onTheme);
            if (this.chart) { this.chart.destroy(); this.chart = null; }
        },

        async setRange(range) {
            if (this.loading || range === this.snapshot.range) return;
            this.loading = true;
            try {
                const res = await fetch(
                    `/api/stock/${encodeURIComponent(this.snapshot.symbol)}?range=${encodeURIComponent(range)}`);
                const data = await res.json();
                if (!data.is_error) {
                    this.snapshot = data;
                    this.$nextTick(() => this.drawChart());
                }
            } catch (e) {
                console.warn('[StockCard] range fetch failed', e);
            } finally {
                this.loading = false;
            }
        },

        drawChart() {
            const canvas = this.$refs.canvas;
            if (!canvas || !window.Chart) return;
            if (this.chart) this.chart.destroy();

            const values = this.snapshot.values || [];
            const up = (this.snapshot.change_pct || 0) >= 0;
            const line = up ? '#34d399' : '#fb7185';
            // Axis colors from the active palette (readable on light themes too).
            const tc = (window.HN && window.HN.chartColors)
                ? window.HN.chartColors()
                : { tick: 'rgba(255,255,255,0.35)', grid: 'rgba(255,255,255,0.06)' };

            const gradient = canvas.getContext('2d').createLinearGradient(0, 0, 0, 170);
            gradient.addColorStop(0, up ? 'rgba(52,211,153,0.28)' : 'rgba(251,113,133,0.28)');
            gradient.addColorStop(1, 'rgba(0,0,0,0)');

            this.chart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: this.snapshot.labels || [],
                    datasets: [{
                        data: values,
                        borderColor: line,
                        backgroundColor: gradient,
                        borderWidth: 2,
                        fill: true,
                        tension: 0.25,
                        pointRadius: 0,
                        pointHoverRadius: 4,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => this.fmtPrice(ctx.parsed.y),
                            },
                        },
                    },
                    scales: {
                        x: {
                            grid: { display: false },
                            ticks: {
                                color: tc.tick,
                                font: { size: 9 },
                                maxTicksLimit: 8,
                                maxRotation: 0,
                            },
                        },
                        y: {
                            position: 'right',
                            grid: { color: tc.grid },
                            ticks: {
                                color: tc.tick,
                                font: { size: 9 },
                                maxTicksLimit: 5,
                            },
                        },
                    },
                },
            });
        },

        fmtPrice(v) {
            if (v === null || v === undefined) return '—';
            const currency = this.snapshot.currency || 'USD';
            try {
                return new Intl.NumberFormat('en-US', {
                    style: 'currency', currency, maximumFractionDigits: 2,
                }).format(v);
            } catch {
                return `${v}`;
            }
        },

        technicalRows() {
            const t = this.snapshot.technicals || {};
            const rsi = t.rsi_14;
            // RSI only means something at the extremes — colour it there, stay
            // neutral in the middle so the eye isn't drawn to a non-signal.
            const rsiTone = rsi == null ? '' :
                rsi >= 70 ? 'text-rose-400' : rsi <= 30 ? 'text-emerald-400' : '';
            const vs50 = t.vs_sma_50;
            return [
                { label: 'RSI (14)', value: rsi, tone: rsiTone },
                { label: 'SMA 20', value: this.fmtNum(t.sma_20), tone: '' },
                { label: 'SMA 50', value: this.fmtNum(t.sma_50), tone: '' },
                { label: 'SMA 200', value: this.fmtNum(t.sma_200), tone: '' },
                {
                    label: 'vs SMA50', value: vs50 == null ? null : `${vs50 > 0 ? '+' : ''}${vs50}%`,
                    tone: vs50 == null ? '' : (vs50 >= 0 ? 'text-emerald-400' : 'text-rose-400'),
                },
                { label: 'Volatility', value: t.volatility == null ? null : `${t.volatility}%`, tone: '' },
                { label: '52w High', value: this.fmtNum(t.week52_high), tone: '' },
                { label: '52w Low', value: this.fmtNum(t.week52_low), tone: '' },
                { label: '52w Range', value: t.week52_position == null ? null : `${t.week52_position}%`, tone: '' },
                { label: 'Volume', value: this.fmtCompact(t.volume), tone: '' },
            ];
        },

        fundamentalRows() {
            const f = this.snapshot.fundamentals || {};
            const rec = f.recommendation;
            const recTone = !rec ? '' :
                /buy/.test(rec) ? 'text-emerald-400' : /sell/.test(rec) ? 'text-rose-400' : '';
            // _yahoo_fundamentals() serves these PRE-FORMATTED as strings — market_cap
            // is already "4.62T", profit_margin already "27.15%". Do not run a number
            // formatter over them: Intl.NumberFormat("4.62T") is NaN. (technicalRows()
            // above is the opposite case — those arrive as raw floats and must be
            // formatted here.)
            const growth = parseFloat(f.revenue_growth);
            return [
                { label: 'Mkt Cap', value: f.market_cap, tone: '' },
                { label: 'P/E', value: f.pe_ratio, tone: '' },
                { label: 'Fwd P/E', value: f.forward_pe, tone: '' },
                { label: 'EPS', value: f.eps, tone: '' },
                { label: 'Beta', value: f.beta, tone: '' },
                { label: 'Div Yield', value: f.dividend_yield, tone: '' },
                { label: 'Revenue', value: f.revenue, tone: '' },
                {
                    label: 'Rev Growth', value: f.revenue_growth,
                    tone: Number.isNaN(growth) ? '' : (growth >= 0 ? 'text-emerald-400' : 'text-rose-400'),
                },
                { label: 'Margin', value: f.profit_margin, tone: '' },
                { label: 'Target', value: f.analyst_target, tone: '' },
                { label: 'Rating', value: rec ? rec.replace(/_/g, ' ') : null, tone: recTone },
                { label: 'Sector', value: f.sector, tone: '' },
            ];
        },

        hasFundamentals() {
            return Object.values(this.snapshot.fundamentals || {}).some(v => v);
        },

        fmtNum(v) {
            return v == null ? null : Number(v).toFixed(2);
        },

        fmtCompact(v) {
            if (v == null) return null;
            return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v);
        },
    }));

    // Crypto token card. Same shape as the stock card: snapshot baked in server-
    // side, range tabs re-fetch /api/crypto/<coin_id> directly (no agent turn).
    Alpine.data('cryptoCardWidget', (initial) => ({
        snapshot: initial || {},
        ranges: ['1d', '7d', '30d', '90d', '1y', 'max'],
        loading: false,
        chart: null,

        init() {
            this.$nextTick(() => this.drawChart());
            this._onTheme = () => this.$nextTick(() => this.drawChart());
            window.addEventListener('hn:theme', this._onTheme);
        },

        destroy() {
            if (this._onTheme) window.removeEventListener('hn:theme', this._onTheme);
            if (this.chart) { this.chart.destroy(); this.chart = null; }
        },

        async setRange(range) {
            if (this.loading || range === this.snapshot.range || !this.snapshot.coin_id) return;
            this.loading = true;
            try {
                const res = await fetch(
                    `/api/crypto/${encodeURIComponent(this.snapshot.coin_id)}?range=${encodeURIComponent(range)}`);
                const data = await res.json();
                if (!data.is_error) {
                    this.snapshot = data;
                    this.$nextTick(() => this.drawChart());
                }
            } catch (e) {
                console.warn('[CryptoCard] range fetch failed', e);
            } finally {
                this.loading = false;
            }
        },

        drawChart() {
            const canvas = this.$refs.canvas;
            if (!canvas || !window.Chart) return;
            if (this.chart) this.chart.destroy();
            const values = this.snapshot.values || [];
            const up = (this.snapshot.change_pct ?? 0) >= 0;
            const line = up ? '#34d399' : '#fb7185';
            const tc = (window.HN && window.HN.chartColors)
                ? window.HN.chartColors()
                : { tick: 'rgba(255,255,255,0.35)', grid: 'rgba(255,255,255,0.06)' };
            const gradient = canvas.getContext('2d').createLinearGradient(0, 0, 0, 150);
            gradient.addColorStop(0, up ? 'rgba(52,211,153,0.28)' : 'rgba(251,113,133,0.28)');
            gradient.addColorStop(1, 'rgba(0,0,0,0)');
            this.chart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: this.snapshot.labels || [],
                    datasets: [{
                        data: values, borderColor: line, backgroundColor: gradient,
                        borderWidth: 2, fill: true, tension: 0.25,
                        pointRadius: 0, pointHoverRadius: 4,
                    }],
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: (ctx) => this.fmtPrice(ctx.parsed.y) } },
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: tc.tick, font: { size: 9 }, maxTicksLimit: 8, maxRotation: 0 } },
                        y: { position: 'right', grid: { color: tc.grid }, ticks: { color: tc.tick, font: { size: 9 }, maxTicksLimit: 5 } },
                    },
                },
            });
        },

        // Prices span 9 orders of magnitude ($60k BTC vs $0.000002 PEPE), so pick
        // precision by size rather than a fixed 2 dp.
        fmtPrice(v) {
            if (v === null || v === undefined) return '—';
            const a = Math.abs(v);
            let dp = 2;
            if (a > 0 && a < 1) dp = Math.min(10, Math.max(2, Math.ceil(-Math.log10(a)) + 3));
            try {
                return new Intl.NumberFormat('en-US', {
                    style: 'currency', currency: 'USD', maximumFractionDigits: dp,
                }).format(v);
            } catch { return `$${v}`; }
        },

        statRows() {
            const s = this.snapshot;
            const rows = [{ label: 'Market Cap', value: s.market_cap || '—' },
                          { label: 'Volume 24h', value: s.volume || '—' }];
            // DexScreener-sourced microcaps carry liquidity but no ATH/high/low;
            // canonical CoinGecko coins carry the reverse. Show whichever exist.
            if (s.liquidity && s.liquidity !== '—') {
                rows.push({ label: 'Liquidity', value: s.liquidity });
                if (s.dex) rows.push({ label: 'DEX', value: s.dex });
            } else {
                rows.push({ label: '24h High', value: s.high_24h || '—' });
                rows.push({ label: '24h Low', value: s.low_24h || '—' });
                rows.push({ label: 'ATH', value: s.ath || '—' });
                if (s.ath_change_pct !== null && s.ath_change_pct !== undefined) {
                    rows.push({ label: 'From ATH', value: `${s.ath_change_pct}%` });
                }
            }
            return rows;
        },
    }));

    // Resolve the persistence key from the WIDGET ROOT, not $el. Inside an
    // x-for template (checklist rows) — and in any nested-element handler —
    // Alpine evaluates $el as the element the expression ran on (a checkbox,
    // a textarea), which has no id, so saves landed under the 'x' fallback
    // key while init() read the root-id key: edits saved, restore missed.
    function widgetStorageId(el) {
        if (!el) return 'x';
        const root = el.closest ? el.closest('.widget-container') : null;
        return (root && root.id) || el.id || 'x';
    }

    Alpine.data('checklistWidget', (title, initialItems = []) => {
        // Snapshot the server baseline BEFORE anything can mutate it. `items`
        // must NOT alias initialItems: Alpine's reactive proxy wraps the same
        // underlying array, so a done-toggle would also mutate the "baseline"
        // we compare against on restore — the saved state then always looked
        // stale and the server won, wiping the user's edits (caught live in a
        // browser check, not by unit tests).
        const baseline = JSON.stringify(Array.isArray(initialItems) ? initialItems : []);
        return {
        title: title || 'Checklist',
        items: JSON.parse(baseline),
        newItem: '',

        // User edits (added tasks, done toggles) live only in Alpine memory and
        // canvas serialization deliberately strips the expanded <li>s, so a
        // reload restored the ORIGINAL list — every checked box and added task
        // silently vanished. Same localStorage pattern as notesWidget: persist
        // keyed by widget id with the server baseline; if the SERVER items
        // changed (the agent rewrote the list), the server wins.
        init() {
            const s = this.load();
            if (s && Array.isArray(s.items) && s.base === baseline) {
                this.items = s.items;
            } else {
                this.persist();
            }
        },

        addTask() {
            const taskText = this.newItem.trim();
            if (taskText) {
                this.items.push({ text: taskText, done: false });
                this.newItem = '';
                this.persist();
            }
        },

        removeTask(index) {
            this.items.splice(index, 1);
            this.persist();
        },

        toggleTask(index) {
            if (this.items[index]) {
                this.items[index].done = !this.items[index].done;
                this.persist();
            }
        },

        _key() { return 'hn_checklist_' + widgetStorageId(this.$el); },
        persist() {
            try {
                localStorage.setItem(this._key(), JSON.stringify({
                    items: JSON.parse(JSON.stringify(this.items)),
                    base: baseline,
                }));
            } catch (e) {}
        },
        load() { try { return JSON.parse(localStorage.getItem(this._key()) || 'null'); } catch (e) { return null; } }
        };
    });

    // 2. Clock Widget — three modes: 'clock' (default), 'stopwatch', 'countdown'
    Alpine.data('clockWidget', (initialTimezone = 'local', mode = 'clock', durationSeconds = 0) => ({
        time: '--:--:--',
        date: '---',
        interval: null,
        selectedTimezone: initialTimezone || 'local',
        mode: ['clock', 'stopwatch', 'countdown'].includes(mode) ? mode : 'clock',
        running: false,
        elapsedMs: 0,               // stopwatch
        baseDurationMs: Math.max(0, Number(durationSeconds) || 0) * 1000,
        remainingMs: Math.max(0, Number(durationSeconds) || 0) * 1000,
        finished: false,            // countdown hit zero
        _lastTick: null,

        init() {
            if (this.selectedTimezone && this.selectedTimezone !== 'local' && this.selectedTimezone !== 'None' && this.selectedTimezone !== 'null') {
                try {
                    Intl.DateTimeFormat(undefined, { timeZone: this.selectedTimezone });
                } catch(e) {
                    this.selectedTimezone = 'local';
                }
            } else {
                this.selectedTimezone = 'local';
            }

            if (this.mode === 'countdown' && this.baseDurationMs > 0) {
                this.running = true;   // a requested timer starts immediately
            }
            this._lastTick = Date.now();
            this.updateTime();
            this.interval = setInterval(() => this.updateTime(), 250);

            this.$watch('selectedTimezone', () => {
                this.updateTime();
            });
        },

        destroy() {
            if (this.interval) clearInterval(this.interval);
        },

        toggle() {
            if (this.mode === 'countdown' && this.finished) this.reset();
            this.running = !this.running;
            this._lastTick = Date.now();
        },

        reset() {
            this.running = false;
            this.finished = false;
            this.elapsedMs = 0;
            this.remainingMs = this.baseDurationMs;
            this.updateTime();
        },

        _fmt(ms) {
            const totalSec = Math.max(0, Math.floor(ms / 1000));
            const h = Math.floor(totalSec / 3600);
            const m = Math.floor((totalSec % 3600) / 60);
            const s = totalSec % 60;
            const pad = (n) => String(n).padStart(2, '0');
            return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
        },

        updateTime() {
            const now = Date.now();
            const delta = now - (this._lastTick || now);
            this._lastTick = now;

            if (this.mode === 'stopwatch') {
                if (this.running) this.elapsedMs += delta;
                this.time = this._fmt(this.elapsedMs);
                this.date = this.running ? 'STOPWATCH · RUNNING' : 'STOPWATCH · PAUSED';
                return;
            }

            if (this.mode === 'countdown') {
                if (this.running && !this.finished) {
                    this.remainingMs -= delta;
                    if (this.remainingMs <= 0) {
                        this.remainingMs = 0;
                        this.running = false;
                        this.finished = true;
                    }
                }
                this.time = this._fmt(this.remainingMs);
                this.date = this.finished ? "⏰ TIME'S UP"
                    : (this.running ? 'COUNTDOWN · RUNNING' : 'COUNTDOWN · PAUSED');
                return;
            }

            const nowDate = new Date();
            const optionsTime = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
            const optionsDate = { weekday: 'short', month: 'short', day: 'numeric' };

            if (this.selectedTimezone !== 'local') {
                optionsTime.timeZone = this.selectedTimezone;
                optionsDate.timeZone = this.selectedTimezone;
            }

            // toLocaleTimeString THROWS (RangeError) on an invalid timeZone — the
            // real throw is here, not on the assignment above. Guard it so one bad
            // timezone can't freeze the whole clock at --:--:-- every tick; fall
            // back to local time instead.
            try {
                this.time = nowDate.toLocaleTimeString([], optionsTime);
                this.date = nowDate.toLocaleDateString([], optionsDate);
            } catch (e) {
                this.selectedTimezone = 'local';
                this.time = nowDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
                this.date = nowDate.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
            }
        }
    }));

    // 3. Notes Widget
    // 3. Notes — markdown editor with edit/preview, interactive checklists,
    // tables, tags, and Save-to-Obsidian-vault. Accepts an options object;
    // legacy positional notesWidget('title','content') still resolves (old
    // canvases rehydrate through the same component).
    Alpine.data('notesWidget', (cfgOrTitle = {}, legacyContent = '') => {
        const cfg = (typeof cfgOrTitle === 'object' && cfgOrTitle !== null)
            ? cfgOrTitle
            : { title: cfgOrTitle, content: legacyContent };
        return {
            title: cfg.title || 'Quick Notes',
            content: cfg.content || '',
            tags: Array.isArray(cfg.tags) ? cfg.tags : [],
            slug: cfg.slug || '',
            mode: 'edit', tagInput: '',
            saving: false, saved: '', dirty: false,

            init() {
                // Restore unsaved local edits for THIS widget id (typed content
                // isn't captured by canvas serialization, so it would be lost on
                // reload). But if the SERVER content changed since the draft was
                // saved (the agent rewrote the note), the server wins — compare
                // against the baseline stored with the draft.
                const s = this.load();
                if (s && (s.content || s.title) && s.base === (cfg.content || '')) {
                    this.title = s.title ?? this.title;
                    this.content = s.content ?? this.content;
                    this.tags = Array.isArray(s.tags) ? s.tags : this.tags;
                    this.slug = s.slug || this.slug;
                } else {
                    // Fresh, or the agent changed it: adopt the server content as
                    // the new baseline so future edits autosave against it.
                    this._persist();
                }
            },

            insert(snippet) {
                const ta = this.$refs.ta;
                if (ta && typeof ta.selectionStart === 'number') {
                    const a = ta.selectionStart, b = ta.selectionEnd;
                    this.content = this.content.slice(0, a) + snippet + this.content.slice(b);
                    this.$nextTick(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = a + snippet.length; });
                } else {
                    this.content += (this.content && !this.content.endsWith('\n') ? '\n' : '') + snippet;
                }
                this.autosave();
            },

            addTag() {
                const t = (this.tagInput || '').trim().replace(/,+$/, '');
                if (t && !this.tags.includes(t)) { this.tags.push(t); this.autosave(); }
                this.tagInput = '';
            },
            removeTag(i) { this.tags.splice(i, 1); this.autosave(); },

            rendered() {
                const src = this.content || '_Nothing yet — switch to Edit._';
                try {
                    let html = window.marked ? marked.parse(src, { gfm: true, breaks: true })
                                             : this.esc(src);
                    // Enable the task-list checkboxes marked renders disabled.
                    html = html.replace(/<input([^>]*?)\sdisabled(="")?([^>]*?)>/g, '<input$1$3>');
                    if (window.DOMPurify)
                        html = DOMPurify.sanitize(html, { ADD_TAGS: ['input'], ADD_ATTR: ['type', 'checked'] });
                    return html;
                } catch (e) { return this.esc(src); }
            },

            onPreviewClick(e) {
                const box = e.target;
                if (!box || box.type !== 'checkbox') return;
                e.preventDefault();
                const boxes = [...e.currentTarget.querySelectorAll('input[type=checkbox]')];
                this.toggleTask(boxes.indexOf(box));
            },

            // Toggle the Nth "- [ ] / - [x]" in the source (order matches preview).
            toggleTask(idx) {
                let i = -1;
                this.content = this.content.replace(/(-\s*\[)([ xX])(\])/g, (m, p1, c, p3) => {
                    i++;
                    if (i !== idx) return m;
                    return p1 + (c === ' ' ? 'x' : ' ') + p3;
                });
                this.autosave();
            },

            async save() {
                this.saving = true; this.saved = '';
                try {
                    const res = await fetch('/api/notes/save', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: this.title, content: this.content, tags: this.tags, slug: this.slug }),
                    });
                    const d = await res.json();
                    if (d && d.ok) { this.slug = d.slug; this.dirty = false; this.saved = '✓ Saved to vault'; this.autosave(); }
                    else this.saved = 'Save failed';
                } catch (e) { this.saved = 'Save failed'; }
                this.saving = false;
                if (this.saved.startsWith('✓')) setTimeout(() => { this.saved = ''; }, 2500);
            },

            esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; },
            _key() { return 'hn_note_' + widgetStorageId(this.$el); },
            _persist() {
                try {
                    localStorage.setItem(this._key(), JSON.stringify({
                        title: this.title, content: this.content, tags: this.tags,
                        slug: this.slug, base: cfg.content || '',
                    }));
                } catch (e) {}
            },
            autosave() { this.dirty = true; this._persist(); },
            load() { try { return JSON.parse(localStorage.getItem(this._key()) || 'null'); } catch (e) { return null; } },
        };
    });

    // 3d. Reminder / alarm — counts down to a target, then notifies + beeps.
    // Client-side (fires while the tab is open); target persists in localStorage
    // so a reload restores it.
    Alpine.data('reminderWidget', (cfg = {}) => ({
        label: cfg.label || 'Reminder',
        target: 0, remaining: '--:--', done: false, timer: null, permHint: '',

        init() {
            const saved = this.load();
            if (saved && saved.target > Date.now()) {
                this.target = saved.target; this.label = saved.label || this.label;
            } else {
                this.target = this.computeTarget(cfg);
            }
            this.save();
            this.requestPerm();
            this.tick();
            this.timer = setInterval(() => this.tick(), 1000);
        },

        destroy() { if (this.timer) clearInterval(this.timer); },

        computeTarget(c) {
            if (c.offset_seconds > 0) return Date.now() + c.offset_seconds * 1000;
            if (c.at_time) {
                const [h, m] = c.at_time.split(':').map(Number);
                const d = new Date(); d.setHours(h, m, 0, 0);
                if (c.tomorrow) d.setDate(d.getDate() + 1);
                else if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1); // passed today → tomorrow
                return d.getTime();
            }
            return Date.now() + 10 * 60 * 1000; // no time given → default 10 min
        },

        tick() {
            const ms = this.target - Date.now();
            if (ms <= 0 && !this.done) this.fire();
            this.remaining = this.fmt(Math.max(0, ms));
        },

        fmt(ms) {
            const s = Math.floor(ms / 1000);
            const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
            return (h ? h + ':' : '') + String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
        },

        fire() {
            this.done = true;
            if (this.timer) { clearInterval(this.timer); this.timer = null; }
            this.beep();
            try {
                if (window.Notification && Notification.permission === 'granted')
                    new Notification('⏰ ' + this.label, { body: 'Reminder', silent: false });
            } catch (e) {}
        },

        beep() {
            try {
                const AC = window.AudioContext || window.webkitAudioContext;
                if (!AC) return;
                const ctx = new AC();
                [0, 0.35, 0.7].forEach(t => {
                    const o = ctx.createOscillator(), g = ctx.createGain();
                    o.connect(g); g.connect(ctx.destination);
                    o.type = 'sine'; o.frequency.value = 880;
                    g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
                    g.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + t + 0.02);
                    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.28);
                    o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.3);
                });
            } catch (e) {}
        },

        snooze(min) {
            this.target = Date.now() + min * 60 * 1000;
            this.done = false; this.save();
            if (this.timer) clearInterval(this.timer);
            this.tick(); this.timer = setInterval(() => this.tick(), 1000);
        },

        dismiss() {
            this.done = false;
            try { localStorage.removeItem(this._key()); } catch (e) {}
            const el = this.$el;
            if (el && window.WidgetManager) window.WidgetManager.dismiss(el);
        },

        requestPerm() {
            try {
                if (!window.Notification) { this.permHint = ''; return; }
                if (Notification.permission === 'default')
                    Notification.requestPermission().then(p => {
                        if (p !== 'granted') this.permHint = 'Allow notifications to be alerted';
                    });
                else if (Notification.permission === 'denied')
                    this.permHint = 'Notifications blocked — the beep still plays';
            } catch (e) {}
        },

        targetLabel() {
            try { return new Date(this.target).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
            catch (e) { return ''; }
        },

        _key() { return 'hn_reminder_' + widgetStorageId(this.$el); },
        save() { try { localStorage.setItem(this._key(), JSON.stringify({ target: this.target, label: this.label })); } catch (e) {} },
        load() { try { return JSON.parse(localStorage.getItem(this._key()) || 'null'); } catch (e) { return null; } },
    }));

    // 3c. Converter — calculator + unit + currency. Fully client-side so each
    // calculation is instant (no agent turn); the server only seeds the initial
    // tab + input from the user's phrasing.
    const _U = {   // conversion factors to each category's base unit
        Length: { mm: 0.001, cm: 0.01, m: 1, km: 1000, in: 0.0254, ft: 0.3048, yd: 0.9144, mi: 1609.344, nmi: 1852 },
        Mass:   { mg: 0.001, g: 1, kg: 1000, t: 1e6, oz: 28.349523, lb: 453.59237, st: 6350.29318 },
        Volume: { ml: 0.001, l: 1, tsp: 0.00492892, tbsp: 0.0147868, cup: 0.236588, pt: 0.473176, qt: 0.946353, gal: 3.785412, floz: 0.0295735 },
        Speed:  { 'm/s': 1, 'km/h': 0.277778, mph: 0.44704, knot: 0.514444, 'ft/s': 0.3048 },
        Area:   { cm2: 0.0001, m2: 1, km2: 1e6, ft2: 0.092903, acre: 4046.8564, ha: 10000, mi2: 2589988.11 },
        Data:   { B: 1, KB: 1024, MB: 1048576, GB: 1073741824, TB: 1099511627776 },
        Time:   { sec: 1, min: 60, hr: 3600, day: 86400, week: 604800 },
        // Placeholder factors — Temperature converts via formula (convTemp),
        // the keys just populate the dropdown.
        Temperature: { '°C': 1, '°F': 1, K: 1 },
    };
    const _SYM = { '$': 'USD', '€': 'EUR', '£': 'GBP', '¥': 'JPY', '₹': 'INR' };

    function _evalMath(raw) {
        let s = (raw || '').trim().toLowerCase();
        // Percent phrases first, then bare percents.
        s = s.replace(/(\d+\.?\d*)\s*%\s*of\s*(\d+\.?\d*)/g, '($1/100*$2)');
        s = s.replace(/(\d+\.?\d*)\s*%\s*off\s*(\d+\.?\d*)/g, '($2*(1-$1/100))');
        s = s.replace(/(\d+\.?\d*)\s*%\s*(?:on|plus|added to)\s*(\d+\.?\d*)/g, '($2*(1+$1/100))');
        s = s.replace(/(\d+\.?\d*)\s*%/g, '($1/100)');
        s = s.replace(/\bx\b/g, '*').replace(/×/g, '*').replace(/÷/g, '/');
        const toks = s.match(/(\d+\.?\d*|[+\-*/^()%])/g);
        if (!toks) throw new Error('empty');
        const prec = { '+': 1, '-': 1, '*': 2, '/': 2, '%': 2, '^': 3 }, right = { '^': 1 };
        const out = [], ops = []; let prev = null;
        for (const t of toks) {
            if (/^\d/.test(t)) { out.push(parseFloat(t)); prev = 'num'; }
            else if (t === '(') { ops.push(t); prev = '('; }
            else if (t === ')') {
                while (ops.length && ops[ops.length - 1] !== '(') out.push(ops.pop());
                if (!ops.length) throw new Error('paren'); ops.pop(); prev = ')';
            } else {
                if (t === '-' && (prev === null || prev === '(' || prev === 'op')) out.push(0);
                while (ops.length && ops[ops.length - 1] !== '(' &&
                    (prec[ops[ops.length - 1]] > prec[t] ||
                        (prec[ops[ops.length - 1]] === prec[t] && !right[t]))) out.push(ops.pop());
                ops.push(t); prev = 'op';
            }
        }
        while (ops.length) { const o = ops.pop(); if (o === '(') throw new Error('paren'); out.push(o); }
        const st = [];
        for (const t of out) {
            if (typeof t === 'number') st.push(t);
            else {
                const b = st.pop(), a = st.pop();
                if (a === undefined || b === undefined) throw new Error('expr');
                st.push(t === '+' ? a + b : t === '-' ? a - b : t === '*' ? a * b :
                    t === '/' ? a / b : t === '%' ? a % b : Math.pow(a, b));
            }
        }
        if (st.length !== 1 || !isFinite(st[0])) throw new Error('expr');
        return st[0];
    }
    function _fmt(n) {
        if (n === '' || n === null || n === undefined || !isFinite(n)) return '—';
        const r = Math.round(n * 1e6) / 1e6;
        return r.toLocaleString('en-US', { maximumFractionDigits: 6 });
    }

    Alpine.data('converterWidget', (cfg = {}) => ({
        tab: cfg.tab || 'calc',
        units: _U,
        // calc
        expr: '', calcResult: '0', calcErr: '',
        // units
        uCat: 'Length', uVal: 1, uFrom: 'mi', uTo: 'km', uResult: '—',
        // currency
        currencies: ['USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'CNY', 'INR', 'MXN', 'BRL'],
        cVal: 1, cFrom: 'USD', cTo: 'EUR', cResult: '—', fxNote: '', rates: null,

        init() {
            const parsed = this.parseSeed(cfg.seed || '');
            this.tab = parsed.tab || this.tab;
            Object.assign(this, parsed.state || {});   // uCat drives the option lists
            // A <select x-model> bound to options from x-for can't reflect a value
            // that was set before those options rendered — it falls back to the
            // first option. Clear the select-bound fields, then restore them on
            // the next tick (options now exist) so each select shows the right one.
            const sels = ['uFrom', 'uTo', 'cFrom', 'cTo'];
            const keep = {};
            sels.forEach(k => { keep[k] = this[k]; this[k] = ''; });
            this.$nextTick(() => {
                sels.forEach(k => { this[k] = keep[k]; });
                this.$nextTick(() => {
                    if (this.tab === 'calc') this.calc();
                    else if (this.tab === 'units') this.conv();
                    else this.loadFx();
                });
            });
        },

        // Route the seed to a tab; return the field values to apply in $nextTick.
        parseSeed(seed) {
            const s = (seed || '').trim();
            if (!s) return { tab: this.tab, state: {} };
            // currency: "20 usd to eur", "$50 in gbp"
            const cur = s.match(/([\d.,]+)\s*([a-z]{3}|[$€£¥₹])\s*(?:to|in|into|=|→)?\s*([a-z]{3}|[$€£¥₹])/i);
            if (cur) {
                const from = (_SYM[cur[2]] || cur[2]).toUpperCase();
                const to = (_SYM[cur[3]] || cur[3]).toUpperCase();
                if (!this.currencies.includes(from)) this.currencies.push(from);
                if (!this.currencies.includes(to)) this.currencies.push(to);
                return { tab: 'currency',
                         state: { cVal: parseFloat(cur[1].replace(/,/g, '')) || 1, cFrom: from, cTo: to } };
            }
            // units: "5 miles in km"
            const alias = {
                miles: 'mi', mile: 'mi', kilometers: 'km', kilometres: 'km', meters: 'm', metres: 'm',
                feet: 'ft', foot: 'ft', inches: 'in', inch: 'in', yards: 'yd', pounds: 'lb', pound: 'lb',
                kilograms: 'kg', grams: 'g', ounces: 'oz', ounce: 'oz', litres: 'l', liters: 'l',
                gallons: 'gal', gallon: 'gal', cups: 'cup', celsius: '°C', fahrenheit: '°F', kelvin: 'K',
                minutes: 'min', hours: 'hr', seconds: 'sec', days: 'day', weeks: 'week',
            };
            const um = s.match(/([\d.,]+)\s*([a-z°]+\/?[a-z]*2?)\s*(?:to|in|into|=|→)\s*([a-z°]+\/?[a-z]*2?)/i);
            if (um) {
                const tnorm = { c: '°C', f: '°F', k: 'K', '°c': '°C', '°f': '°F' };
                const norm = u => { const a = alias[u.toLowerCase()] || u; return tnorm[a.toLowerCase()] || a; };
                const from = norm(um[2]), to = norm(um[3]);
                for (const [cat, tbl] of Object.entries(this.units)) {
                    if (tbl[from] !== undefined && tbl[to] !== undefined) {
                        return { tab: 'units',
                                 state: { uCat: cat, uFrom: from, uTo: to, uVal: parseFloat(um[1].replace(/,/g, '')) || 1 } };
                    }
                }
            }
            return { tab: 'calc', state: { expr: s } };
        },

        calc() {
            if (!this.expr.trim()) { this.calcResult = '0'; this.calcErr = ''; return; }
            try { this.calcResult = _fmt(_evalMath(this.expr)); this.calcErr = ''; }
            catch (e) { this.calcErr = 'Not a valid expression'; }
        },

        onCatChange() {
            const keys = Object.keys(this.units[this.uCat]);
            this.uFrom = keys[0]; this.uTo = keys[1] || keys[0]; this.conv();
        },

        conv() {
            const v = parseFloat(this.uVal);
            if (!isFinite(v)) { this.uResult = '—'; return; }
            if (this.uCat === 'Temperature') { this.uResult = _fmt(this.convTemp(v, this.uFrom, this.uTo)); return; }
            const tbl = this.units[this.uCat];
            const base = v * tbl[this.uFrom];
            this.uResult = _fmt(base / tbl[this.uTo]);
        },

        convTemp(v, from, to) {
            const f = from.replace('°', '').toUpperCase(), t = to.replace('°', '').toUpperCase();
            const c = f === 'C' ? v : f === 'F' ? (v - 32) * 5 / 9 : v - 273.15;   // to Celsius
            return t === 'C' ? c : t === 'F' ? c * 9 / 5 + 32 : c + 273.15;
        },

        async loadFx() {
            try {
                const res = await fetch(`/api/fx/${encodeURIComponent(this.cFrom)}`);
                const data = await res.json();
                if (data && data.rates) {
                    this.rates = data.rates;
                    this.fxNote = data.updated ? `Rates ${data.updated}` : '';
                    // widen the currency list to everything the API returned
                    const all = Object.keys(data.rates).sort();
                    this.currencies = [...new Set([this.cFrom, ...this.currencies, ...all])]
                        .filter(c => c === this.cFrom || all.includes(c));
                } else { this.fxNote = 'Rates unavailable'; }
            } catch (e) { this.fxNote = 'Rates unavailable'; this.rates = null; }
            this.fxConv();
        },

        fxConv() {
            const v = parseFloat(this.cVal);
            if (!isFinite(v) || !this.rates || !this.rates[this.cTo]) { this.cResult = '—'; return; }
            this.cResult = _fmt(v * this.rates[this.cTo]);
        },
    }));

    // 3b. Settings — appearance (theme swatches) + preferences. The agent pops
    // this up; all controls act on the live page through window.HN, so a click
    // here changes the theme/mute for real, no round-trip.
    Alpine.data('settingsWidget', (cfg = {}) => ({
        themes: (cfg && cfg.themes) || [],
        active: (cfg && cfg.active) || 'hud',
        muted: false,

        init() {
            // The agent asked for a specific theme this turn → apply it now.
            if (cfg && cfg.apply && window.HN && window.HN.applyTheme) {
                window.HN.applyTheme(cfg.apply);
                this.active = cfg.apply;
            } else if (window.HN && window.HN.currentTheme) {
                this.active = window.HN.currentTheme();
            }
            this.muted = !!(window.HN && window.HN.isMuted && window.HN.isMuted());
            // Keep the toggle honest if mute changes from the command bar.
            this._onMute = (e) => { this.muted = !!(e.detail && e.detail.muted); };
            window.addEventListener('hn:mute', this._onMute);
            // Reflect a theme change made elsewhere (another settings panel).
            this._onTheme = (e) => { this.active = (e.detail && e.detail.name) || this.active; };
            window.addEventListener('hn:theme', this._onTheme);
        },

        destroy() {
            if (this._onMute) window.removeEventListener('hn:mute', this._onMute);
            if (this._onTheme) window.removeEventListener('hn:theme', this._onTheme);
        },

        setTheme(name) {
            if (window.HN && window.HN.applyTheme) window.HN.applyTheme(name);
            this.active = name;
        },

        toggleMute() {
            const next = !this.muted;
            if (window.HN && window.HN.setMuted) window.HN.setMuted(next);
            this.muted = next;
        },

        resetLayout() {
            try {
                localStorage.removeItem('widget_sizes');
                localStorage.removeItem('widget_order');
            } catch (e) {}
            location.reload();
        },
    }));

    // 4. Mini Music Player — a thin client over the music-player service.
    //
    // The genre/artist intelligence lives SERVER-SIDE in the music-player
    // service (:8002): its mix engine discovers artists for arbitrary genres
    // (LLM + MusicBrainz/Wikidata/AudioDB) and strictly verifies each YouTube
    // hit. This widget used to second-guess it with a hardcoded genre list and
    // a local-library matcher — which is how "jungle" (not on the list) turned
    // into an artist search and personal library tracks. Now the service
    // decides, over its SSE mix endpoint, and this side just owns the queue.
    //
    // Accepts an options object {genre, kind, autoplay, base}. Legacy
    // positional calls musicPlayerWidget('jazz', true) still resolve — nodes
    // rendered before this change rehydrate through the same component.
    Alpine.data('musicPlayerWidget', (cfgOrGenre = {}, legacyAutoplay = false) => {
        const cfg = (typeof cfgOrGenre === 'object' && cfgOrGenre !== null)
            ? cfgOrGenre
            : { genre: cfgOrGenre, autoplay: legacyAutoplay };
        return {
        queue: [],
        currentIndex: -1,
        isPlaying: false,
        audio: null,
        error: '',
        genreFilter: cfg.genre || '',
        // '' | 'genre' | 'artist' — routing's guess; '' means try genre first.
        kind: cfg.kind || '',
        autoplayWanted: !!cfg.autoplay,
        base: cfg.base || `http://${window.location.hostname}:8002`,
        webBase: cfg.webBase || `http://${window.location.hostname}:3232`,
        es: null,
        streamStatus: '',
        showQueue: false,
        seenIds: null,          // Set — created in init (Alpine clones data objects)
        resolvedType: 'genre',  // whichever mix type actually filled the queue
        triedArtistFallback: false,
        refillInFlight: false,
        lastRefillAt: 0,
        currentTime: 0,
        duration: 0,
        // Consecutive tracks the CDN refused. YouTube serves only the first
        // ~1MB of most files without a PO token, so a queue is now a mix of
        // playable and dead tracks; skipping finds the playable ones. Bounded
        // so an all-dead queue reports instead of spinning.
        deadInARow: 0,
        maxDeadInARow: 8,
        // Pending tab handoff: keep playing until the other tab confirms.
        handoffPending: null,
        handoffTimer: null,
        pruneInFlight: false,
        isShuffle: false,
        isRepeat: false,
        volume: 1.0,
        isMuted: false,
        prevVolume: 1.0,

        get currentTrack() {
            if (this.currentIndex >= 0 && this.currentIndex < this.queue.length) {
                return this.queue[this.currentIndex];
            }
            return null;
        },

        // Rows for the queue panel: everything after the current track, with
        // absolute indices preserved so playAt/removeAt address the real array.
        get upcoming() {
            return this.queue.map((t, i) => ({ t, i })).slice(this.currentIndex + 1);
        },

        get progress() {
            return this.duration ? (this.currentTime / this.duration) * 100 : 0;
        },

        async init() {
            console.log(`[MusicPlayer] Init. term="${this.genreFilter}" kind="${this.kind}" base=${this.base}`);
            this.seenIds = new Set();
            this.audio = new Audio();
            this.audio.volume = this.volume;

            // Audio Event Listeners. Each handler guards on this.audio:
            // destroy() nulls it, but events already queued on the old element
            // (a final timeupdate especially) still fire afterwards.
            this.audio.addEventListener('ended', () => {
                if (!this.audio) return;
                if (this.isRepeat) {
                    this.audio.currentTime = 0;
                    this.audio.play();
                } else {
                    this.nextTrack({ auto: true });
                }
                this.maybeRefill();
            });
            this.audio.addEventListener('play', () => {
                this.isPlaying = true;
            });
            this.audio.addEventListener('pause', () => {
                this.isPlaying = false;
            });
            this.audio.addEventListener('timeupdate', () => {
                if (this.audio) this.currentTime = this.audio.currentTime;
            });
            this.audio.addEventListener('durationchange', () => {
                if (this.audio) this.duration = this.audio.duration || 0;
            });
            this.audio.addEventListener('error', (e) => {
                if (!this.audio || !this.audio.src) return;
                console.error('[MusicPlayer] Native audio playback error:', e);
                this.handleStreamError();
            });
            this.audio.addEventListener('playing', () => {
                this.notePlaybackStarted();
            });

            const term = this.genreFilter || 'lo-fi';
            // Routing tags "X music" phrasing kind=genre and named acts
            // kind=artist. Unknown → genre first: the genre pipeline is strict
            // (returns nothing rather than garbage), so a band name that isn't
            // a genre falls through to the artist mix in failover().
            this.startStream(term, this.kind === 'artist' ? 'artist' : 'genre');
        },

        // Every fetch gets a hard timeout so a hung endpoint can never
        // leave the widget stuck on "Searching signals...".
        async fetchJson(url, timeoutMs = 12000) {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), timeoutMs);
            try {
                const res = await fetch(url, { signal: ctrl.signal });
                if (!res.ok) return null;
                return await res.json();
            } catch (e) {
                console.warn(`[MusicPlayer] Fetch failed/timed out: ${url}`, e);
                return null;
            } finally {
                clearTimeout(timer);
            }
        },

        asTrack(v) {
            return {
                id: v.id,
                title: v.title,
                artist: v.artist || v.uploader || 'YouTube Music',
                path: v.id,
                isYoutube: true
            };
        },

        /** Append fresh (unseen) tracks to the queue. Returns how many landed. */
        enqueue(raw) {
            const fresh = (raw || [])
                .filter(v => v && v.id && !this.seenIds.has(v.id))
                .map(v => this.asTrack(v));
            fresh.forEach(t => this.seenIds.add(t.id));
            this.queue.push(...fresh);
            if (fresh.length) this.pruneAhead();
            return fresh.length;
        },

        /**
         * Consume the music-player mix over SSE: tracks arrive artist-by-artist
         * as the pipeline resolves them, so playback starts on the FIRST batch
         * instead of racing a timeout against the whole ~18s cold pipeline
         * (the race that used to end in Burzum for "smooth jazz").
         * type=artist is served by the same endpoint as a single tracks+done
         * pair, so one code path covers both kinds.
         */
        startStream(term, type, { refresh = false } = {}) {
            this.closeStream();
            this.resolvedType = type;
            this.streamStatus = 'Tuning in…';
            const url = `${this.base}/api/youtube/mix/${encodeURIComponent(term)}/stream`
                      + `?type=${type}${refresh ? '&refresh=true' : ''}`;
            let gotTracks = false;
            let transportRetried = false;
            // The cold genre pipeline emits its first tracks batch well inside
            // 60s; silence past that means it's wedged, not slow.
            const watchdog = setTimeout(() => {
                if (!gotTracks) { this.closeStream(); this.failover(term, type); }
            }, 60000);

            this.es = new EventSource(url);
            this.es.addEventListener('status', e => {
                try { this.streamStatus = JSON.parse(e.data).message || ''; } catch {}
            });
            this.es.addEventListener('tracks', e => {
                let d = {};
                try { d = JSON.parse(e.data); } catch { return; }
                const landed = this.enqueue(d.tracks);
                if (d.progress) this.streamStatus = `Loading artists ${d.progress}`;
                if (!gotTracks && landed > 0) {
                    gotTracks = true;
                    this.error = '';
                    this.playAt(this.currentIndex >= 0 ? this.currentIndex : 0, { auto: true });
                }
            });
            this.es.addEventListener('done', () => {
                clearTimeout(watchdog);
                this.streamStatus = '';
                // CRITICAL: close on done. EventSource auto-reconnects after a
                // server-side close, which would re-run the entire discovery
                // pipeline in a loop and burn the service's 10/min mix limit.
                this.closeStream();
                if (!gotTracks) this.failover(term, type);
            });
            this.es.addEventListener('error', () => {
                // Server-sent terminal error event (named "error" in the SSE
                // protocol of the mix endpoint) — distinct from transport onerror.
                clearTimeout(watchdog);
                this.closeStream();
                if (!gotTracks) this.failover(term, type);
            });
            this.es.onerror = () => {
                // Transport-level failure. Allow ONE built-in reconnect (flaky
                // proxy blip); a second means the service is down or the stream
                // closed uncleanly — stop and fail over if nothing played yet.
                if (!this.es) return;
                if (gotTracks) { clearTimeout(watchdog); this.closeStream(); return; }
                if (!transportRetried) { transportRetried = true; return; }
                clearTimeout(watchdog);
                this.closeStream();
                this.failover(term, type, { transportDead: true });
            };
        },

        closeStream() {
            if (this.es) {
                this.es.close();
                this.es = null;
            }
        },

        /**
         * Fallback ladder, in order of honesty:
         *   genre miss → artist mix (covers "Jungle" the band routed as genre)
         *   artist miss → plain YouTube search
         *   still nothing + user named something → say so and STOP. Never dump
         *     the personal library for a named genre — that's the Burzum bug.
         *   bare query only → full local library.
         *   service unreachable → offline message (the library lives on the
         *     same host, so it can't help either).
         */
        async failover(term, type, { transportDead = false } = {}) {
            this.streamStatus = '';
            if (transportDead) {
                this.error = 'Music service is offline.';
                return;
            }
            if (type === 'genre' && !this.triedArtistFallback) {
                this.triedArtistFallback = true;
                console.warn(`[MusicPlayer] Genre mix empty for "${term}" — retrying as artist.`);
                this.startStream(term, 'artist');
                return;
            }
            // Plain search: cheap, returns the single top hit.
            const searchData = await this.fetchJson(
                `${this.base}/api/youtube/search?query=${encodeURIComponent(term + ' music')}`, 15000);
            const hits = Array.isArray(searchData) ? searchData : (searchData ? [searchData] : []);
            if (this.enqueue(hits) > 0) {
                this.playAt(0, { auto: true });
                return;
            }
            if (this.genreFilter) {
                this.error = `Couldn't find "${term}" — try a different genre or artist.`;
                return;
            }
            // Bare rehydrated widget with no query at all: the library is fine.
            const localData = await this.fetchJson(`${this.base}/api/tracks`, 15000);
            const local = ((localData && localData.tracks) || [])
                .map(t => ({ id: t.path || t.id, title: t.title, artist: t.artist, path: t.path, isYoutube: false }));
            if (local.length) {
                this.queue.push(...local);
                this.playAt(0, { auto: true });
            } else {
                this.error = 'No tracks found. Music service may be offline.';
            }
        },

        /**
         * Background refill when the queue runs low. refresh=true re-runs the
         * discovery pipeline (slow, but we're already playing); enqueue()'s
         * seenIds dedupe keeps repeats out. Floored at 90s between attempts —
         * the mix endpoint is rate-limited to 10/min service-side.
         */
        maybeRefill() {
            const remaining = this.queue.length - this.currentIndex - 1;
            if (remaining > 5 || this.refillInFlight || !this.genreFilter) return;
            if (Date.now() - this.lastRefillAt < 90000) return;
            this.refillInFlight = true;
            this.lastRefillAt = Date.now();
            const term = this.genreFilter;
            this.fetchJson(
                `${this.base}/api/youtube/mix/${encodeURIComponent(term)}?type=${this.resolvedType}&refresh=true`, 60000)
                .then(d => {
                    const landed = this.enqueue((d && d.videos) || []);
                    if (landed) console.log(`[MusicPlayer] Refilled queue with ${landed} tracks.`);
                })
                .finally(() => { this.refillInFlight = false; });
        },

        loadTrack() {
            this.pruneAhead();
            if (!this.currentTrack) return;
            if (!this.audio) {
                this.audio = new Audio();
            }
            this.audio.volume = this.isMuted ? 0 : this.volume;
            if (this.currentTrack.isYoutube) {
                this.audio.src = `${this.base}/api/youtube/stream/${encodeURIComponent(this.currentTrack.id)}`;
            } else {
                const encodedPath = encodeURIComponent(this.currentTrack.path);
                this.audio.src = `${this.base}/api/music/stream?path=${encodedPath}`;
            }
            this.maybeRefill();
        },

        /** Jump to an absolute queue index. The one entry point for playback. */
        async playAt(i, { auto = false } = {}) {
            if (!auto) this.cancelHandoff();
            if (i < 0 || i >= this.queue.length) return;
            // Settle on a track that will actually stream BEFORE touching the
            // audio element. Pruning ahead is not enough on its own: when most
            // of a queue is refused, playback burns through the unprobed tail
            // faster than the background probes complete, and every one of
            // those is an audible failure (measured: 9 in a row, on a queue
            // that still held playable tracks).
            const target = await this.settleOnPlayable(i);
            if (target < 0) {
                this.error = 'Nothing in this queue would play. The source is refusing these streams.';
                this.streamStatus = '';
                this.isPlaying = false;
                return;
            }
            this.currentIndex = target;
            this.loadTrack();
            const shouldPlay = auto ? this.autoplayWanted : true;
            if (shouldPlay) {
                this.audio.play().catch(e => {
                    console.warn('[MusicPlayer] Autoplay prevented by browser policy.', e);
                    this.isPlaying = false;
                });
            }
        },

        /** Remove an upcoming track. The playing track isn't removable. */
        removeAt(i) {
            if (i === this.currentIndex || i < 0 || i >= this.queue.length) return;
            this.queue.splice(i, 1);
            if (i < this.currentIndex) this.currentIndex--;
        },

        playPause() {
            this.cancelHandoff();
            if (!this.audio.src) return;
            if (this.audio.paused) {
                this.audio.play();
            } else {
                this.audio.pause();
            }
        },

        nextTrack({ auto = false } = {}) {
            // Auto-advance (track ended, or a stream the CDN refused) must NOT
            // abandon a pending handoff — only a deliberate "next" does.
            if (!auto) this.cancelHandoff();
            if (this.queue.length === 0) return;
            const wasPlaying = this.isPlaying;
            this.currentIndex = (this.currentIndex + 1) % this.queue.length;
            this.loadTrack();
            if (wasPlaying) this.audio.play();
        },

        prevTrack() {
            this.cancelHandoff();
            if (this.queue.length === 0) return;
            // Standard player behavior: >3s in, "previous" means restart.
            if (this.audio && this.audio.currentTime > 3) {
                this.audio.currentTime = 0;
                return;
            }
            const wasPlaying = this.isPlaying;
            this.currentIndex = (this.currentIndex - 1 + this.queue.length) % this.queue.length;
            this.loadTrack();
            if (wasPlaying) this.audio.play();
        },

        seek(percent) {
            if (this.audio && this.duration) {
                this.audio.currentTime = (percent / 100) * this.duration;
            }
        },

        handleSeek(e) {
            const rect = e.currentTarget.getBoundingClientRect();
            const percent = ((e.clientX - rect.left) / rect.width) * 100;
            this.seek(percent);
        },

        // Walk forward from `i` until a track probes playable, discarding the
        // refused ones as it goes. Returns the index to play, or -1.
        async settleOnPlayable(i, maxProbes = 25) {
            let probes = 0;
            while (i < this.queue.length && probes < maxProbes) {
                const t = this.queue[i];
                if (!t || !t.isYoutube || t.probed) return i;
                t.probed = true;
                probes++;
                if (await this.probePlayable(t.id)) return i;
                if (this.queue[i] === t) {
                    this.queue.splice(i, 1);   // dead: the next slides into i
                    this.streamStatus = 'Skipping tracks the source refused...';
                } else {
                    i++;                        // queue moved under us
                }
            }
            return i < this.queue.length ? i : -1;
        },

        // Drop tracks the CDN will not serve BEFORE they are heard.
        //
        // Roughly three quarters of YouTube tracks currently return only their
        // first ~1MB (AUDIO_PIPELINE.md item 4), and the reactive skip in
        // handleStreamError only reacts once a track has already failed —
        // audible as a stutter. Probing the upcoming few turns that into
        // nothing at all. The probe lives on the music-player API because the
        // one request that discriminates, `Range: bytes=0-`, needs a CORS
        // preflight from a browser.
        async pruneAhead(lookahead = 4) {
            if (this.pruneInFlight) return;
            this.pruneInFlight = true;
            try {
                for (let n = 1; n <= lookahead; n++) {
                    const i = this.currentIndex + n;
                    if (i >= this.queue.length) break;
                    const t = this.queue[i];
                    if (!t || !t.isYoutube || t.probed) continue;
                    t.probed = true;
                    const playable = await this.probePlayable(t.id);
                    if (!playable && this.queue[i] === t) {
                        this.queue.splice(i, 1);
                        n--;  // the next track slid into this slot
                    }
                }
            } finally {
                this.pruneInFlight = false;
            }
        },

        async probePlayable(id) {
            if (!id) return true;
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), 20000);
            try {
                const res = await fetch(`${this.base}/api/youtube/playable/${encodeURIComponent(id)}`,
                                        { signal: ctrl.signal });
                if (!res.ok) return true;
                const d = await res.json();
                return d.playable !== false;
            } catch (e) {
                // Unknown, not dead — a blip must never discard a good track.
                return true;
            } finally {
                clearTimeout(timer);
            }
        },

        // A stream the CDN will not serve must not end the session: step to the
        // next track and keep playing. Called by the audio `error` listener.
        handleStreamError() {
            // destroy() sets src='' which itself fires `error` — skipping on
            // that would resurrect a widget the user just closed.
            if (!this.audio || !this.audio.src) return;

            this.deadInARow++;
            const canSkip = this.queue.length > 1 && this.deadInARow <= this.maxDeadInARow;
            if (canSkip) {
                this.error = '';
                this.streamStatus = 'Skipping a track the source refused...';
                this.nextTrack({ auto: true });
                // nextTrack only resumes when it thought it was playing; a
                // failed track may already have cleared that.
                if (!this.isPlaying && this.audio) this.audio.play();
                return;
            }
            this.error = this.queue.length > 1
                ? 'Nothing in this queue would play. The source is refusing these streams.'
                : 'Audio playback error.';
            this.isPlaying = false;
        },

        // A track that actually plays clears the budget, so a later bad patch
        // gets its own full set of retries.
        notePlaybackStarted() {
            this.deadInARow = 0;
        },

        // Hand the current track off to the full music-player app: open it at
        // the same position, then stop playing here so the two are not doubled.
        // The widget itself stays on the canvas so playback can resume locally.
        openInFullPlayer() {
            const track = this.currentTrack;
            if (!track) return;

            let url = this.webBase;
            let handoffId = null;
            if (track.isYoutube) {
                handoffId = this.newHandoffId();
                // Read the position BEFORE pausing.
                const params = new URLSearchParams({
                    track: track.id,
                    t: String(Math.floor((this.audio && this.audio.currentTime) || 0)),
                    autoplay: '1',
                    handoff: handoffId,
                });
                if (track.title) params.set('title', track.title);
                if (track.artist) params.set('artist', track.artist);
                if (this.genreFilter) params.set('genre', this.genreFilter);
                url = `${this.webBase}/?${params.toString()}`;
            }

            // Synchronous, so it counts as the user's gesture and no popup
            // blocker fires. With noopener the handle is null even on success,
            // so nothing below may depend on the returned handle.
            window.open(url, '_blank', 'noopener,noreferrer');

            if (!handoffId) {
                // Nothing to hand over (local file) — just stop.
                if (this.audio && !this.audio.paused) this.audio.pause();
                return;
            }
            this.awaitHandoff(handoffId);
        },

        newHandoffId() {
            if (window.crypto && window.crypto.randomUUID) {
                return window.crypto.randomUUID().replace(/-/g, '');
            }
            return `h${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
        },

        // KEEP PLAYING until the other tab is genuinely playing, then stop.
        //
        // The new tab is a different origin and cannot inherit the click that
        // opened it, so the browser usually refuses to autoplay there. Pausing
        // on faith is what produced silence on BOTH sides: this side stopped,
        // that side never started. The rendezvous record on the music-player
        // API is the only channel the two tabs share.
        //
        // While waiting we park the live position, so the other tab resumes
        // from where the music ACTUALLY got to, not from where it was when the
        // link was built.
        awaitHandoff(handoffId, timeoutMs = 45000) {
            const base = this.base;
            const started = Date.now();
            this.handoffPending = handoffId;
            this.streamStatus = 'Opening in Music Player...';

            const stop = () => {
                if (this.handoffTimer) clearInterval(this.handoffTimer);
                this.handoffTimer = null;
                this.handoffPending = null;
            };

            const tick = async () => {
                // A new track here means the listener moved on; the handoff is
                // stale and must not silence what they are playing now.
                if (this.handoffPending !== handoffId) return stop();
                if (Date.now() - started > timeoutMs) {
                    this.streamStatus = '';
                    return stop();
                }
                const position = (this.audio && this.audio.currentTime) || 0;
                try {
                    const res = await fetch(`${base}/api/handoff/${handoffId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ track: this.currentTrack ? this.currentTrack.id : null, position }),
                    });
                    const rec = res.ok ? await res.json() : null;
                    // Re-check AFTER the await: the listener may have picked a
                    // different track while this request was in flight, and a
                    // late "started" must not silence what they just chose.
                    if (this.handoffPending !== handoffId) return stop();
                    if (rec && rec.started) {
                        stop();
                        this.streamStatus = 'Playing in Music Player';
                        if (this.audio && !this.audio.paused) this.audio.pause();
                    }
                } catch (e) {
                    // The other tab may still be loading; keep playing and retry.
                }
            };

            if (this.handoffTimer) clearInterval(this.handoffTimer);
            this.handoffTimer = setInterval(tick, 700);
            tick();
        },

        // Any deliberate change of track abandons a pending handoff — otherwise
        // the other tab starting later would pause music the listener just
        // chose here.
        cancelHandoff() {
            this.handoffPending = null;
            if (this.handoffTimer) clearInterval(this.handoffTimer);
            this.handoffTimer = null;
        },

        setVolume(vol) {
            this.volume = parseFloat(vol);
            this.isMuted = (this.volume === 0);
            if (this.audio) {
                this.audio.volume = this.isMuted ? 0 : this.volume;
            }
        },

        toggleMute() {
            if (this.isMuted) {
                this.isMuted = false;
                this.volume = this.prevVolume || 1.0;
            } else {
                this.prevVolume = this.volume;
                this.isMuted = true;
                this.volume = 0;
            }
            if (this.audio) {
                this.audio.volume = this.isMuted ? 0 : this.volume;
            }
        },

        toggleShuffle() {
            // Shuffle only what hasn't played: history order stays intact and
            // the current track doesn't jump out from under the listener.
            this.isShuffle = !this.isShuffle;
            if (!this.isShuffle) return;
            const head = this.queue.slice(0, this.currentIndex + 1);
            const tail = this.queue.slice(this.currentIndex + 1);
            for (let i = tail.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [tail[i], tail[j]] = [tail[j], tail[i]];
            }
            this.queue = [...head, ...tail];
        },

        toggleRepeat() {
            this.isRepeat = !this.isRepeat;
        },

        formatTime(sec) {
            if (!sec || isNaN(sec)) return '0:00';
            const m = Math.floor(sec / 60);
            const s = Math.floor(sec % 60);
            return `${m}:${s < 10 ? '0' : ''}${s}`;
        },

        destroy() {
            // Fires on close-button dismissal AND when the media singleton
            // swaps this widget for a new one. Closing the SSE stream here is
            // load-bearing: an orphaned EventSource keeps auto-reconnecting
            // and re-running the discovery pipeline against the rate limit.
            this.closeStream();
            this.cancelHandoff();
            if (this.audio) {
                this.audio.pause();
                this.audio.src = '';
                this.audio = null;
            }
        }
        };
    });

    // 5. YouTube Player Widget
    // candidates: alternate video ids to try when a video refuses to embed
    // (music-label videos frequently block embedding — error 101/150).
    Alpine.data('youtubePlayerWidget', (initialVideoId = '', title = 'YouTube Player', candidates = [], searchQuery = '') => ({
        videoId: initialVideoId,
        embedUrl: '',
        isLoading: false,
        error: '',
        watchUrl: '',
        title: title || 'YouTube Player',
        candidates: Array.isArray(candidates) ? candidates.filter(Boolean) : [],
        searchQuery: searchQuery || '',
        attemptedIds: [],
        fetchedMoreCandidates: false,
        player: null,

        init() {
            if (this.videoId) {
                this.resolveVideo();
            }
        },

        destroy() {
            this.destroyPlayer();
        },

        destroyPlayer() {
            if (this.player) {
                try { this.player.destroy(); } catch (e) {}
                this.player = null;
            }
        },

        async resolveVideo() {
            let query = this.videoId;
            if (query.startsWith('query:')) {
                query = query.substring(6);
            }

            const isYtId = /^[a-zA-Z0-9_-]{11}$/.test(query);
            const isUrl = query.includes('youtube.com') || query.includes('youtu.be');

            if (isYtId) {
                this.playById(query);
                return;
            }

            if (isUrl) {
                const extracted = this.extractYoutubeId(query);
                if (extracted) {
                    this.playById(extracted);
                    return;
                }
            }

            if (query && query.trim() !== '') {
                this.searchQuery = this.searchQuery || query;
                this.isLoading = true;
                this.error = '';
                try {
                    const res = await fetch(`/api/youtube/candidates?query=${encodeURIComponent(query)}`);
                    if (res.ok) {
                        const data = await res.json();
                        const results = (data && data.results) || [];
                        if (results.length > 0) {
                            this.candidates = results.map(r => r.id).filter(Boolean);
                            if (results[0].title) this.title = results[0].title;
                            this.playById(this.candidates.shift());
                        } else {
                            this.error = 'No videos found for this search.';
                        }
                    } else {
                        this.error = 'Failed to find video.';
                    }
                } catch (err) {
                    this.error = 'Search connection error.';
                } finally {
                    this.isLoading = false;
                }
            }
        },

        playById(id) {
            this.attemptedIds.push(id);
            this.videoId = id;
            this.error = '';
            this.watchUrl = '';
            this.destroyPlayer();
            // Toggle embedUrl through '' so x-if rebuilds a fresh iframe for
            // each attempt — the IFrame API binds to one iframe per video.
            this.embedUrl = '';
            this.$nextTick(() => {
                // mute=1 is REQUIRED for autoplay to fire at all: browsers block
                // UNMUTED autoplay inside a cross-origin iframe, so autoplay=1 on
                // its own just loads the video paused (the "video won't play until
                // I click it" complaint). Start muted so it actually plays, then
                // unmute via the IFrame API in onReady below.
                this.embedUrl = `https://www.youtube.com/embed/${id}?autoplay=1&mute=1&enablejsapi=1&origin=${encodeURIComponent(window.location.origin)}`;
                this.$nextTick(() => this.watchForEmbedErrors());
            });
        },

        async watchForEmbedErrors() {
            try {
                await loadYouTubeIframeApi();
                const iframe = this.$el.querySelector('iframe');
                if (!iframe || !window.YT || !window.YT.Player) return;
                this.player = new YT.Player(iframe, {
                    events: {
                        onReady: (e) => {
                            // The src autoplays muted (browser rule). Now that it
                            // is playing, try to unmute: browsers permit a
                            // programmatic unmute once the page has seen a user
                            // gesture, and sending the chat message that spawned
                            // this widget IS one. If a given browser refuses, it
                            // stays muted-but-playing instead of paused — strictly
                            // better than before.
                            try { e.target.playVideo(); } catch (_) {}
                            try { e.target.unMute(); e.target.setVolume(100); } catch (_) {}
                        },
                        onError: (e) => this.handleEmbedError(e.data)
                    }
                });
            } catch (e) {
                console.warn('[YouTubePlayer] IFrame API unavailable, embed errors will not auto-recover:', e);
            }
        },

        async handleEmbedError(code) {
            console.warn(`[YouTubePlayer] Embed error ${code} for ${this.videoId} — trying next candidate.`);
            // 101/150 = embedding disabled, 100 = removed/private, 2 = bad id
            let next = this.candidates.find(id => !this.attemptedIds.includes(id));
            if (!next && !this.fetchedMoreCandidates && (this.searchQuery || this.title)) {
                this.fetchedMoreCandidates = true;
                try {
                    const q = this.searchQuery || this.title;
                    const res = await fetch(`/api/youtube/candidates?query=${encodeURIComponent(q)}&limit=8`);
                    if (res.ok) {
                        const data = await res.json();
                        this.candidates.push(...((data && data.results) || []).map(r => r.id).filter(Boolean));
                        next = this.candidates.find(id => !this.attemptedIds.includes(id));
                    }
                } catch (e) {}
            }
            if (next) {
                this.playById(next);
            } else {
                this.destroyPlayer();
                this.embedUrl = '';
                this.watchUrl = `https://www.youtube.com/watch?v=${this.attemptedIds[0] || this.videoId}`;
                this.error = 'This video blocks embedding.';
            }
        },

        extractYoutubeId(url) {
            const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|\&v=)([^#\&\?]*).*/;
            const match = url.match(regExp);
            return (match && match[2].length === 11) ? match[2] : null;
        }
    }));

    // Confirm card for a destructive container action. The action is already
    // parked server-side; this button is the ONLY thing that fires it, and the
    // server consumes the pending id so a double-click cannot run it twice.
    Alpine.data('actionConfirmWidget', (initial) => ({
        pendingId: (initial && initial.pendingId) || '',
        label: (initial && initial.label) || '',
        busy: false,
        done: false,
        ok: false,
        message: '',

        async run() {
            if (this.busy || this.done || !this.pendingId) return;
            this.busy = true;
            try {
                const res = await fetch('/api/actions/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pending_id: this.pendingId }),
                });
                const data = await res.json();
                this.ok = Boolean(data && data.success);
                this.message = this.ok
                    ? `${this.label} started.`
                    : (data && (data.error || data.detail)) || 'Action failed.';
            } catch (e) {
                this.ok = false;
                this.message = 'Could not reach the server.';
            } finally {
                this.busy = false;
                this.done = true;
            }
        },

        cancel() {
            this.done = true;
            this.ok = false;
            this.message = 'Cancelled — nothing ran.';
            // Tell the server to drop it so it cannot be fired later.
            fetch('/api/actions/cancel', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pending_id: this.pendingId }),
            }).catch(() => {});
        },
    }));

    // App Hub launcher grid. Initial app list is baked server-side (paints
    // immediately); a 45s poll of /api/services refreshes status dots and picks
    // up newly registered services IN PLACE — Alpine state only, never a canvas
    // repaint, so live media elsewhere on the canvas is untouched.
    Alpine.data('appGridWidget', (initial) => ({
        apps: (initial && initial.apps) || [],
        stale: Boolean(initial && initial.stale),
        _timer: null,

        init() {
            this._timer = setInterval(() => this.refresh(), 45000);
        },

        destroy() {
            if (this._timer) { clearInterval(this._timer); this._timer = null; }
        },

        visibleApps() {
            return this.apps.filter(a => !a.hidden && a.launch_url);
        },

        async refresh() {
            try {
                const res = await fetch('/api/services');
                if (!res.ok) return;
                const data = await res.json();
                if (data && Array.isArray(data.apps)) {
                    this.apps = data.apps;
                    this.stale = Boolean(data.stale);
                }
            } catch (e) {
                // Portal or html-notes hiccup — keep showing what we have.
                this.stale = true;
            }
        },

        async _override(app, body) {
            try {
                const res = await fetch(`/api/services/${encodeURIComponent(app.id)}/override`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (res.ok) this.refresh();
            } catch (e) {
                console.warn('[AppGrid] override failed', e);
            }
        },

        togglePin(app) {
            app.pinned = !app.pinned;   // optimistic; refresh() reconciles
            this._override(app, { pinned: app.pinned });
        },

        hideApp(app) {
            app.hidden = true;
            this._override(app, { hidden: true });
        },
    }));

});
