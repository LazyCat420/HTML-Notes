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
    Alpine.data('clockWidget', (timezone = 'local') => ({
        time: '--:--:--',
        date: '---',
        interval: null,
        
        init() {
            this.updateTime();
            this.interval = setInterval(() => this.updateTime(), 1000);
        },
        
        destroy() {
            if (this.interval) clearInterval(this.interval);
        },
        
        updateTime() {
            const now = new Date();
            const optionsTime = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
            const optionsDate = { weekday: 'short', month: 'short', day: 'numeric' };
            
            if (timezone !== 'local') {
                optionsTime.timeZone = timezone;
                optionsDate.timeZone = timezone;
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
            this.audio = new Audio();
            this.audio.addEventListener('ended', () => this.nextTrack());
            this.audio.addEventListener('play', () => this.isPlaying = true);
            this.audio.addEventListener('pause', () => this.isPlaying = false);
            this.audio.addEventListener('error', (e) => {
                this.error = 'Audio playback error.';
                this.isPlaying = false;
            });

            try {
                // The music-player backend is hosted on port 8002 of the current host
                const host = window.location.hostname;
                const response = await fetch(`http://${host}:8002/api/tracks`);
                if (!response.ok) throw new Error('Failed to fetch tracks');
                
                const data = await response.json();
                let loadedTracks = data.tracks || [];

                // Apply genre filter if specified
                if (this.genreFilter) {
                    const term = this.genreFilter.toLowerCase();
                    loadedTracks = loadedTracks.filter(t => 
                        (t.genre && t.genre.toLowerCase().includes(term)) ||
                        (t.title && t.title.toLowerCase().includes(term)) ||
                        (t.artist && t.artist.toLowerCase().includes(term))
                    );
                }

                if (loadedTracks.length === 0) {
                    this.error = 'No tracks found for this genre.';
                    return;
                }

                // Shuffle array slightly
                this.tracks = loadedTracks.sort(() => Math.random() - 0.5);
                this.currentIndex = 0;
                this.loadTrack();

                if (autoplay) {
                    this.audio.play().catch(e => {
                        console.warn('Autoplay prevented by browser', e);
                        this.isPlaying = false;
                    });
                }
            } catch (err) {
                this.error = 'Could not connect to music server.';
                console.error(err);
            }
        },

        loadTrack() {
            if (!this.currentTrack) return;
            const host = window.location.hostname;
            const encodedPath = encodeURIComponent(this.currentTrack.path);
            this.audio.src = `http://${host}:8002/api/music/stream?path=${encodedPath}`;
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
