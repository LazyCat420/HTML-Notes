// Global Alpine.js widget registry for the Smart Dashboard Lego System

// The mix endpoint has two pipelines: type=genre asks an LLM to discover
// artists for a genre (slow, and wrong for a proper noun like "Oasis" — the
// LLM has nothing to discover, it just burns 10-20s before falling through).
// type=artist tries a direct YouTube search first and only calls the LLM if
// that comes up short, so anything that isn't a recognized genre word should
// go through it instead.
const KNOWN_MUSIC_GENRES = new Set([
    'lofi', 'lo-fi', 'jazz', 'reggae', 'rock', 'pop', 'hiphop', 'hip-hop', 'rap',
    'classical', 'edm', 'techno', 'ambient', 'blues', 'country', 'metal', 'indie',
    'electronic', 'funk', 'soul', 'disco', 'folk', 'punk', 'house', 'trance',
    'dubstep', 'kpop', 'k-pop', 'rnb', 'r&b', 'instrumental', 'chill', 'acoustic',
    'gospel', 'latin', 'salsa', 'reggaeton', 'afrobeat', 'synthwave', 'vaporwave',
    'workout', 'study', 'sleep', 'party', 'romantic', 'oldies', 'grunge', 'ska',
]);

function isKnownMusicGenre(term) {
    return (term || '').toLowerCase().split(/\s+/).some(w => KNOWN_MUSIC_GENRES.has(w));
}

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
                                color: 'rgba(255,255,255,0.35)',
                                font: { size: 9 },
                                maxTicksLimit: 8,
                                maxRotation: 0,
                            },
                        },
                        y: {
                            position: 'right',
                            grid: { color: 'rgba(255,255,255,0.06)' },
                            ticks: {
                                color: 'rgba(255,255,255,0.35)',
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

    Alpine.data('checklistWidget', (title, initialItems = []) => ({
        title: title || 'Checklist',
        items: initialItems,
        newItem: '',
        
        addTask() {
            const taskText = this.newItem.trim();
            if (taskText) {
                this.items.push({ text: taskText, done: false });
                this.newItem = '';
            }
        },
        
        removeTask(index) {
            this.items.splice(index, 1);
        }
    }));

    // 2. Clock Widget
    Alpine.data('clockWidget', (initialTimezone = 'local') => ({
        time: '--:--:--',
        date: '---',
        interval: null,
        selectedTimezone: initialTimezone || 'local',
        
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
            
            this.updateTime();
            this.interval = setInterval(() => this.updateTime(), 1000);
            
            this.$watch('selectedTimezone', () => {
                this.updateTime();
            });
        },
        
        destroy() {
            if (this.interval) clearInterval(this.interval);
        },
        
        updateTime() {
            const now = new Date();
            const optionsTime = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
            const optionsDate = { weekday: 'short', month: 'short', day: 'numeric' };
            
            if (this.selectedTimezone !== 'local') {
                try {
                    optionsTime.timeZone = this.selectedTimezone;
                    optionsDate.timeZone = this.selectedTimezone;
                } catch (e) {}
            }
            
            this.time = now.toLocaleTimeString([], optionsTime);
            this.date = now.toLocaleDateString([], optionsDate);
        }
    }));

    // 3. Notes Widget
    Alpine.data('notesWidget', (title, initialContent = '') => ({
        title: title || 'Quick Notes',
        content: initialContent
    }));

    // 4. Mini Music Player
    Alpine.data('musicPlayerWidget', (genreFilter = '', autoplay = false) => ({
        tracks: [],
        currentIndex: -1,
        isPlaying: false,
        audio: null,
        error: '',
        genreFilter: genreFilter,
        currentTime: 0,
        duration: 0,
        isShuffle: false,
        isRepeat: false,
        volume: 1.0,
        isMuted: false,
        prevVolume: 1.0,

        get currentTrack() {
            if (this.currentIndex >= 0 && this.currentIndex < this.tracks.length) {
                return this.tracks[this.currentIndex];
            }
            return null;
        },

        get progress() {
            return this.duration ? (this.currentTime / this.duration) * 100 : 0;
        },

        async init() {
            console.log(`[MusicPlayer] Initializing widget. Genre Filter: "${this.genreFilter}", Autoplay: ${autoplay}`);
            this.audio = new Audio();
            this.audio.volume = this.volume;
            
            // Audio Event Listeners
            this.audio.addEventListener('ended', () => {
                console.log('[MusicPlayer] Track ended.');
                if (this.isRepeat) {
                    console.log('[MusicPlayer] Repeating single track.');
                    this.audio.currentTime = 0;
                    this.audio.play();
                } else {
                    this.nextTrack();
                }
            });
            this.audio.addEventListener('play', () => {
                this.isPlaying = true;
            });
            this.audio.addEventListener('pause', () => {
                this.isPlaying = false;
            });
            this.audio.addEventListener('timeupdate', () => {
                this.currentTime = this.audio.currentTime;
            });
            this.audio.addEventListener('durationchange', () => {
                this.duration = this.audio.duration || 0;
            });
            this.audio.addEventListener('error', (e) => {
                console.error('[MusicPlayer] Native audio playback error:', e);
                this.error = 'Audio playback error.';
                this.isPlaying = false;
            });

            // Every fetch gets a hard timeout so a hung endpoint can never
            // leave the widget stuck on "Searching signals...".
            const fetchJson = async (url, timeoutMs = 12000) => {
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
            };

            try {
                const host = window.location.hostname;
                const ytGenre = this.genreFilter || "lo-fi";

                const asTrack = (v) => ({
                    id: v.id,
                    title: v.title,
                    artist: v.artist || v.uploader || "YouTube Music",
                    path: v.id,
                    isYoutube: true
                });

                // Both requests go out at once. /api/tracks returns the WHOLE
                // library unpaginated — ~7MB of JSON, seconds to download and
                // parse — and it used to be awaited first, holding up the genre
                // mix, which answers in about 40ms. That wait was the entire
                // "jazz music takes forever" delay: the widget shell renders
                // immediately and then sits on "Searching signals..." until this
                // resolves. The mix alone is enough to start playing.
                const mixType = isKnownMusicGenre(ytGenre) ? 'genre' : 'artist';
                const localPromise = fetchJson(`http://${host}:8002/api/tracks`, 8000);
                const ytPromise = fetchJson(
                    `http://${host}:8002/api/youtube/mix/${encodeURIComponent(ytGenre)}?type=${mixType}`, 15000);

                const matchesGenre = (t) => {
                    if (!this.genreFilter) return true;
                    const term = this.genreFilter.toLowerCase();
                    return (t.genre && t.genre.toLowerCase().includes(term)) ||
                           (t.title && t.title.toLowerCase().includes(term)) ||
                           (t.artist && t.artist.toLowerCase().includes(term));
                };

                const ytData = await ytPromise;
                const ytVideos = (ytData && ytData.videos) || [];

                if (ytVideos.length > 0) {
                    // Play now; fold the local library in once it finally lands.
                    this.tracks = ytVideos.map(asTrack);
                    this.currentIndex = 0;
                    this.loadTrack();
                    if (autoplay) {
                        this.audio.play().catch(e => {
                            console.warn('[MusicPlayer] Autoplay prevented by browser policy.', e);
                            this.isPlaying = false;
                        });
                    }
                    localPromise.then(localData => {
                        const local = ((localData && localData.tracks) || []).filter(matchesGenre);
                        if (local.length) this.tracks = [...this.tracks, ...local];
                    });
                    return;
                }

                // No mix for this genre — fall back to the local library, then a
                // direct YouTube search.
                const localData = await localPromise;
                const allLocalTracks = (localData && localData.tracks) || [];
                let loadedTracks = allLocalTracks.filter(matchesGenre);

                if (loadedTracks.length === 0) {
                    const searchData = await fetchJson(
                        `http://${host}:8002/api/youtube/search?query=${encodeURIComponent(ytGenre + " music")}`, 15000);
                    const hits = Array.isArray(searchData) ? searchData : (searchData ? [searchData] : []);
                    loadedTracks = hits.filter(v => v && v.id).map(asTrack);
                }

                if (loadedTracks.length === 0 && allLocalTracks.length > 0) {
                    console.warn(`[MusicPlayer] Nothing found for "${ytGenre}" — falling back to full library.`);
                    this.error = `Nothing found for "${ytGenre}" — playing your library instead.`;
                    this.genreFilter = '';
                    loadedTracks = allLocalTracks;
                }

                if (loadedTracks.length === 0) {
                    this.error = `No tracks found for "${ytGenre}". Music service may be offline.`;
                    return;
                }

                this.tracks = loadedTracks;
                // A fallback notice shouldn't linger once playback works.
                if (this.error) setTimeout(() => { this.error = ''; }, 6000);

                // Shuffle array initially if isShuffle is on, else keep order
                if (this.isShuffle) {
                    this.tracks = [...loadedTracks].sort(() => Math.random() - 0.5);
                }
                
                this.currentIndex = 0;
                this.loadTrack();

                if (autoplay) {
                    this.audio.play().catch(e => {
                        console.warn('[MusicPlayer] Autoplay prevented by browser policy.', e);
                        this.isPlaying = false;
                    });
                }
            } catch (err) {
                this.error = 'Could not connect to music server.';
                console.error('[MusicPlayer] Fatal initialization error:', err);
            }
        },

        loadTrack() {
            if (!this.currentTrack) return;
            if (!this.audio) {
                this.audio = new Audio();
            }
            this.audio.volume = this.isMuted ? 0 : this.volume;
            const host = window.location.hostname;
            if (this.currentTrack.isYoutube) {
                this.audio.src = `http://${host}:8002/api/youtube/stream/${encodeURIComponent(this.currentTrack.id)}`;
            } else {
                const encodedPath = encodeURIComponent(this.currentTrack.path);
                this.audio.src = `http://${host}:8002/api/music/stream?path=${encodedPath}`;
            }
        },

        playPause() {
            if (!this.audio.src) return;
            if (this.audio.paused) {
                this.audio.play();
            } else {
                this.audio.pause();
            }
        },

        nextTrack() {
            if (this.tracks.length === 0) return;
            if (this.isShuffle) {
                this.currentIndex = Math.floor(Math.random() * this.tracks.length);
            } else {
                this.currentIndex = (this.currentIndex + 1) % this.tracks.length;
            }
            this.loadTrack();
            if (this.isPlaying) this.audio.play();
        },

        prevTrack() {
            if (this.tracks.length === 0) return;
            if (this.isShuffle) {
                this.currentIndex = Math.floor(Math.random() * this.tracks.length);
            } else {
                this.currentIndex = (this.currentIndex - 1 + this.tracks.length) % this.tracks.length;
            }
            this.loadTrack();
            if (this.isPlaying) this.audio.play();
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
            this.isShuffle = !this.isShuffle;
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
            if (this.audio) {
                this.audio.pause();
                this.audio.src = '';
                this.audio = null;
            }
        }
    }));

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
                this.embedUrl = `https://www.youtube.com/embed/${id}?autoplay=1&enablejsapi=1&origin=${encodeURIComponent(window.location.origin)}`;
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
    
});
