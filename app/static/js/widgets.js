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
    
});
