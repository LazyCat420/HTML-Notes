// Global Alpine.js widget registry for the Smart Dashboard Lego System

document.addEventListener('alpine:init', () => {
    
    // 1. Checklist Widget
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
            // validate initialTimezone
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

        get currentTrack() {
            if (this.currentIndex >= 0 && this.currentIndex < this.tracks.length) {
                return this.tracks[this.currentIndex];
            }
            return null;
        },

        async init() {
            console.log(`[MusicPlayer] Initializing widget. Genre Filter: "${this.genreFilter}", Autoplay: ${autoplay}`);
            this.audio = new Audio();
            this.audio.addEventListener('ended', () => {
                console.log('[MusicPlayer] Track ended. Moving to next track.');
                this.nextTrack();
            });
            this.audio.addEventListener('play', () => {
                console.log('[MusicPlayer] Audio playing.');
                this.isPlaying = true;
            });
            this.audio.addEventListener('pause', () => {
                console.log('[MusicPlayer] Audio paused.');
                this.isPlaying = false;
            });
            this.audio.addEventListener('error', (e) => {
                console.error('[MusicPlayer] Native audio playback error:', e);
                this.error = 'Audio playback error.';
                this.isPlaying = false;
            });

            try {
                // The music-player backend is hosted on port 8002 of the current host
                const host = window.location.hostname;
                const localUrl = `http://${host}:8002/api/tracks`;
                
                // Fetch local tracks
                const localRes = await fetch(localUrl);
                let loadedTracks = [];
                if (localRes.ok) {
                    const data = await localRes.json();
                    loadedTracks = data.tracks || [];
                }

                // Apply genre filter if specified
                let ytGenre = this.genreFilter || "lo-fi"; // fallback genre
                if (this.genreFilter) {
                    const term = this.genreFilter.toLowerCase();
                    loadedTracks = loadedTracks.filter(t => 
                        (t.genre && t.genre.toLowerCase().includes(term)) ||
                        (t.title && t.title.toLowerCase().includes(term)) ||
                        (t.artist && t.artist.toLowerCase().includes(term))
                    );
                }

                // Fetch YouTube mix concurrently
                const ytUrl = `http://${host}:8002/api/youtube/mix/${encodeURIComponent(ytGenre)}?type=genre`;
                try {
                    const ytRes = await fetch(ytUrl);
                    if (ytRes.ok) {
                        const ytData = await ytRes.json();
                        const ytVideos = ytData.videos || [];
                        // Normalize YT tracks to match local track structure
                        const normalizedYt = ytVideos.map(v => ({
                            id: v.id,
                            title: v.title,
                            artist: v.artist || v.uploader || "YouTube Music",
                            path: v.id, 
                            isYoutube: true
                        }));
                        loadedTracks = [...loadedTracks, ...normalizedYt];
                    }
                } catch (ytErr) {
                    console.warn('[MusicPlayer] Failed to load YouTube tracks:', ytErr);
                }

                if (loadedTracks.length === 0) {
                    console.warn('[MusicPlayer] No tracks found after filtering and fetching.');
                    this.error = 'No tracks found for this genre.';
                    return;
                }

                // Shuffle array slightly
                this.tracks = loadedTracks.sort(() => Math.random() - 0.5);
                this.currentIndex = 0;
                console.log(`[MusicPlayer] Loading first track: ${this.currentTrack.title}`);
                this.loadTrack();

                if (autoplay) {
                    console.log('[MusicPlayer] Autoplay is true. Attempting to play automatically...');
                    this.audio.play().catch(e => {
                        console.warn('[MusicPlayer] Autoplay prevented by browser policy.', e);
                        this.isPlaying = false;
                    });
                }
            } catch (err) {
                this.error = 'Could not connect to music server. (CORS or Network Error)';
                console.error('[MusicPlayer] Fatal initialization error:', err);
            }
        },

        loadTrack() {
            if (!this.currentTrack) return;
            if (!this.audio) {
                console.warn('[MusicPlayer] Audio element was null in loadTrack. Re-initializing.');
                this.audio = new Audio();
            }
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
            this.currentIndex = (this.currentIndex + 1) % this.tracks.length;
            this.loadTrack();
            if (this.isPlaying) this.audio.play();
        },

        destroy() {
            if (this.audio) {
                this.audio.pause();
                this.audio.src = '';
                this.audio = null;
            }
        }
    }));
    
});
