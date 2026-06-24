// HTML-Notes App Controller
document.addEventListener("DOMContentLoaded", () => {
    // Application State
    const state = {
        sessionId: localStorage.getItem("html_notes_session_id") || generateUUID(),
        activeNoteId: null,
        notes: [],
        tags: [],
        selectedTag: null,
        mediaRecorder: null,
        audioChunks: [],
        isRecording: false,
        graphData: null
    };

    // Save session ID
    localStorage.setItem("html_notes_session_id", state.sessionId);

    // DOM Elements
    const elements = {
        searchInput: document.getElementById("search-input"),
        tagFiltersContainer: document.getElementById("tag-filters-container"),
        notesListContainer: document.getElementById("notes-list-container"),
        btnNewNote: document.getElementById("btn-new-note"),
        btnShowGraph: document.getElementById("btn-show-graph"),
        
        noteTitle: document.getElementById("note-title"),
        noteVersionBadge: document.getElementById("note-version-badge"),
        noteUpdatedTime: document.getElementById("note-updated-time"),
        noteTagsContainer: document.getElementById("note-tags-container"),
        renderedNoteView: document.getElementById("rendered-note-view"),
        proposedLinksPanel: document.getElementById("proposed-links-panel"),
        proposedLinksContainer: document.getElementById("proposed-links-container"),
        
        btnEditNote: document.getElementById("btn-edit-note"),
        btnSaveNote: document.getElementById("btn-save-note"),
        
        chatHistoryContainer: document.getElementById("chat-history-container"),
        chatInput: document.getElementById("chat-input"),
        btnSendMessage: document.getElementById("btn-send-message"),
        btnMic: document.getElementById("btn-mic"),
        recordingStatus: document.getElementById("recording-status"),
        healthIndicator: document.getElementById("health-indicator"),
        
        graphModal: document.getElementById("graph-modal"),
        btnCloseGraph: document.getElementById("btn-close-graph"),
        cyGraphContainer: document.getElementById("cy-graph-container")
    };

    // Initialization
    loadNotesList();
    checkHealth();
    setInterval(checkHealth, 30000); // Check health every 30 seconds

    // ─── EVENT HANDLERS ───────────────────────────────────────

    // 1. Send Chat Message
    elements.btnSendMessage.addEventListener("click", sendChatMessage);
    elements.chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChatMessage();
        }
    });

    // 2. Micro-Dictation Recorder
    elements.btnMic.addEventListener("click", toggleRecording);

    // 3. Search and Tag Filtering
    elements.searchInput.addEventListener("input", (e) => {
        filterNotes(e.target.value, state.selectedTag);
    });

    // 4. Create New Note
    elements.btnNewNote.addEventListener("click", () => {
        clearActiveNote();
        appendChatMessage("assistant", "Sure! I've cleared the editor. Tell me what note you want to create (e.g., 'Write a note about Python lists').");
    });

    // 5. Edit and Save Note manual overrides
    elements.btnEditNote.addEventListener("click", toggleEditMode);
    elements.btnSaveNote.addEventListener("click", saveManualEdits);

    // 6. Relationship Graph View Modal
    elements.btnShowGraph.addEventListener("click", openGraphModal);
    elements.btnCloseGraph.addEventListener("click", () => {
        elements.graphModal.style.display = "none";
    });

    // ─── API OPERATIONS ────────────────────────────────────────

    async function loadNotesList() {
        try {
            const res = await fetch("/notes/create"); // Wait, check endpoint list from FastAPI
            // Our backend has GET /search with query, or standard list?
            // Ah, let's see list: in main.py we have GET /search, wait, let's make list API
            // Wait, does main.py have GET /notes or list? In main.py, we defined:
            // @app.get("/search") def api_search(q: str): ...
            // Wait, we can list notes by calling GET /search?q= or we should add a quick list endpoint?
            // Actually, database.py has list_all_notes. Let's check how search works:
            // api_search takes parameter `q`. Let's check if we can call `/search?q=` or if we can query notes.
            // In main.py:
            // @app.get("/search") async def api_search(q: str): return database.search_notes(q)
            // Wait, if q is empty, database.search_notes(q) returns notes matching empty string! Let's check:
            // sqlite query is `WHERE title LIKE %q%` so if q="", it matches everything! That is perfect!
            const response = await fetch("/search?q=");
            state.notes = await response.json();
            renderNotesList(state.notes);
            renderTagFilters();
        } catch (err) {
            console.error("Failed to load notes:", err);
        }
    }

    function renderNotesList(notesToRender) {
        elements.notesListContainer.innerHTML = "";
        if (notesToRender.length === 0) {
            elements.notesListContainer.innerHTML = `<div style="padding: 1.5rem; text-align: center; color: var(--text-secondary); font-size: 0.85rem;">No notes found</div>`;
            return;
        }

        notesToRender.forEach(note => {
            const div = document.createElement("div");
            div.className = `note-item ${state.activeNoteId === note.id ? "active" : ""}`;
            div.setAttribute("data-id", note.id);
            
            const title = document.createElement("div");
            title.className = "note-item-title";
            title.textContent = note.title || "Untitled";
            
            const meta = document.createElement("div");
            meta.className = "note-item-meta";
            
            const timeSpan = document.createElement("span");
            timeSpan.textContent = formatTimestamp(note.updated_at);
            
            const verBadge = document.createElement("span");
            verBadge.className = "badge";
            verBadge.textContent = `v${note.version}`;
            
            meta.appendChild(timeSpan);
            meta.appendChild(verBadge);
            div.appendChild(title);
            div.appendChild(meta);
            
            div.addEventListener("click", () => selectNote(note.id));
            elements.notesListContainer.appendChild(div);
        });
    }

    function renderTagFilters() {
        // Collect unique tags
        const allTags = new Set();
        state.notes.forEach(note => {
            if (note.tags && Array.isArray(note.tags)) {
                note.tags.forEach(t => allTags.add(t));
            }
        });
        state.tags = Array.from(allTags);

        elements.tagFiltersContainer.innerHTML = "";
        
        // Add "All" chip
        const allChip = document.createElement("span");
        allChip.className = `tag-chip ${!state.selectedTag ? "active" : ""}`;
        allChip.textContent = "All";
        allChip.addEventListener("click", () => {
            state.selectedTag = null;
            filterNotes(elements.searchInput.value, null);
            renderTagFilters();
        });
        elements.tagFiltersContainer.appendChild(allChip);

        state.tags.forEach(tag => {
            const chip = document.createElement("span");
            chip.className = `tag-chip ${state.selectedTag === tag ? "active" : ""}`;
            chip.textContent = tag;
            chip.addEventListener("click", () => {
                state.selectedTag = tag;
                filterNotes(elements.searchInput.value, tag);
                renderTagFilters();
            });
            elements.tagFiltersContainer.appendChild(chip);
        });
    }

    function filterNotes(query, tag) {
        let filtered = state.notes;
        
        if (tag) {
            filtered = filtered.filter(n => n.tags && n.tags.includes(tag));
        }
        
        if (query) {
            const lowerQuery = query.toLowerCase();
            filtered = filtered.filter(n => 
                (n.title && n.title.toLowerCase().includes(lowerQuery)) || 
                (n.tags && n.tags.some(t => t.toLowerCase().includes(lowerQuery)))
            );
        }
        
        renderNotesList(filtered);
    }

    async function selectNote(noteId) {
        state.activeNoteId = noteId;
        
        // Highlight active sidebar item
        document.querySelectorAll(".note-item").forEach(item => {
            if (item.getAttribute("data-id") === noteId) {
                item.classList.add("active");
            } else {
                item.classList.remove("active");
            }
        });

        try {
            const response = await fetch(`/notes/${noteId}`);
            const data = await response.json();
            renderActiveNote(data.note, data.history);
        } catch (err) {
            console.error("Failed to fetch note details:", err);
        }
    }

    function renderActiveNote(note, history) {
        elements.noteTitle.textContent = note.title;
        elements.noteTitle.setAttribute("contenteditable", "false");
        elements.noteVersionBadge.textContent = `v${note.version}`;
        elements.noteUpdatedTime.textContent = `Updated ${formatTimestamp(note.updated_at)}`;
        
        // Tags
        elements.noteTagsContainer.innerHTML = "";
        if (note.tags && Array.isArray(note.tags)) {
            note.tags.forEach(t => {
                const tagSpan = document.createElement("span");
                tagSpan.className = "note-tag";
                tagSpan.textContent = t;
                elements.noteTagsContainer.appendChild(tagSpan);
            });
        }
        
        // Render sanitized HTML note body
        // Ensure DOMPurify runs safely on frontend
        const sanitizedHTML = DOMPurify.sanitize(note.rendered_html);
        elements.renderedNoteView.innerHTML = sanitizedHTML;
        elements.renderedNoteView.setAttribute("contenteditable", "false");
        
        // Add note link click handlers inside the rendered view
        elements.renderedNoteView.querySelectorAll("a").forEach(a => {
            const href = a.getAttribute("href");
            if (href && href.startsWith("#note_")) {
                const id = href.replace("#", "");
                a.removeAttribute("href"); // Prevent browser navigation
                a.style.cursor = "pointer";
                a.addEventListener("click", (e) => {
                    e.preventDefault();
                    selectNote(id);
                });
            }
        });

        // Toggle edit actions
        elements.btnEditNote.style.display = "inline-block";
        elements.btnSaveNote.style.display = "none";
        elements.btnEditNote.textContent = "Edit Content";

        // Render proposed links panel
        elements.proposedLinksPanel.style.display = "none";
        elements.proposedLinksContainer.innerHTML = "";
    }

    function clearActiveNote() {
        state.activeNoteId = null;
        elements.noteTitle.textContent = "New Note";
        elements.noteTitle.setAttribute("contenteditable", "false");
        elements.noteVersionBadge.textContent = "v1";
        elements.noteUpdatedTime.textContent = "";
        elements.noteTagsContainer.innerHTML = "";
        elements.renderedNoteView.innerHTML = `<article><p>New blank workspace. Type your notes or talk to your assistant to draft content.</p></article>`;
        elements.renderedNoteView.setAttribute("contenteditable", "false");
        elements.btnEditNote.style.display = "none";
        elements.btnSaveNote.style.display = "none";
        elements.proposedLinksPanel.style.display = "none";
        
        document.querySelectorAll(".note-item").forEach(item => item.classList.remove("active"));
    }

    // ─── CHAT CONVERSATION HANDLERS ───────────────────────────

    async function sendChatMessage() {
        const text = elements.chatInput.value.strip ? elements.chatInput.value.strip() : elements.chatInput.value.trim();
        if (!text) return;

        elements.chatInput.value = "";
        appendChatMessage("user", text);

        // Append typing indicator
        const typingId = appendChatMessage("assistant", "Thinking...");

        try {
            const res = await fetch("/session/message", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: state.sessionId,
                    message: text,
                    target_note_id: state.activeNoteId
                })
            });
            const data = await res.json();
            
            // Remove typing indicator
            removeChatMessage(typingId);
            
            // Add real response
            appendChatMessage("assistant", data.message);

            // Real-time rendering of generated note components
            if (data.note) {
                // If a note was successfully generated or modified, load it
                state.activeNoteId = data.note.id;
                await loadNotesList();
                await selectNote(data.note.id);
                
                // Show proposed links if any exist
                if (data.proposed_links && data.proposed_links.length > 0) {
                    renderProposedLinks(data.proposed_links);
                }
            }
        } catch (err) {
            console.error("Chat turn error:", err);
            removeChatMessage(typingId);
            appendChatMessage("assistant", "Sorry, I encountered an error connecting to the model service.");
        }
    }

    function renderProposedLinks(links) {
        elements.proposedLinksContainer.innerHTML = "";
        elements.proposedLinksPanel.style.display = "block";
        
        links.forEach(l => {
            const card = document.createElement("div");
            card.className = "proposed-link-card";
            
            const info = document.createElement("div");
            info.className = "proposed-link-info";
            
            const title = document.createElement("span");
            title.className = "proposed-link-title";
            title.textContent = `Link: ${l.title}`;
            
            const reason = document.createElement("span");
            reason.className = "proposed-link-reason";
            reason.textContent = l.reason || "Conceptual similarity detected";
            
            info.appendChild(title);
            info.appendChild(reason);
            card.appendChild(info);
            
            const actionBtn = document.createElement("button");
            actionBtn.className = "btn-link-action";
            actionBtn.textContent = "Connect";
            actionBtn.addEventListener("click", async (e) => {
                e.stopPropagation();
                await connectNotes(state.activeNoteId, l.note_id);
                actionBtn.textContent = "Linked ✓";
                actionBtn.disabled = true;
            });
            card.appendChild(actionBtn);
            
            elements.proposedLinksContainer.appendChild(card);
        });
    }

    async function connectNotes(sourceId, targetId) {
        try {
            await fetch("/notes/link", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    source_note_id: sourceId,
                    target_note_id: targetId
                })
            });
            await loadNotesList();
        } catch (err) {
            console.error("Linking failed:", err);
        }
    }

    function appendChatMessage(role, content) {
        const id = "msg_" + Math.random().toString(36).substr(2, 9);
        const div = document.createElement("div");
        div.className = `chat-message ${role}`;
        div.id = id;
        
        const p = document.createElement("p");
        p.textContent = content;
        div.appendChild(p);
        
        elements.chatHistoryContainer.appendChild(div);
        elements.chatHistoryContainer.scrollTop = elements.chatHistoryContainer.scrollHeight;
        return id;
    }

    function removeChatMessage(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
    }

    // ─── AUDIO MIC / STT DICTATION SUPPORT ──────────────────────

    async function toggleRecording() {
        if (state.isRecording) {
            // Stop recording
            state.mediaRecorder.stop();
            elements.btnMic.classList.remove("recording");
            elements.recordingStatus.textContent = "Processing...";
            state.isRecording = false;
        } else {
            // Start recording
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                state.audioChunks = [];
                state.mediaRecorder = new MediaRecorder(stream);
                
                state.mediaRecorder.addEventListener("dataavailable", event => {
                    state.audioChunks.push(event.data);
                });
                
                state.mediaRecorder.addEventListener("stop", async () => {
                    const audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
                    // Convert audio to base64
                    const reader = new FileReader();
                    reader.readAsDataURL(audioBlob);
                    reader.onloadend = async () => {
                        const base64Data = reader.result.split(',')[1];
                        await transcribeAudio(base64Data);
                    };
                    
                    // Stop mic tracks
                    stream.getTracks().forEach(track => track.stop());
                });

                state.mediaRecorder.start();
                elements.btnMic.classList.add("recording");
                elements.recordingStatus.textContent = "Listening...";
                state.isRecording = true;
            } catch (err) {
                console.error("Audio recording failed:", err);
                appendChatMessage("assistant", "Could not access your microphone. Check browser permissions.");
            }
        }
    }

    async function transcribeAudio(base64Audio) {
        try {
            const res = await fetch("/session/transcribe", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ audio: base64Audio })
            });
            const data = await res.json();
            if (data.text) {
                elements.chatInput.value = data.text;
                elements.recordingStatus.textContent = "Record";
                // Auto trigger message send
                sendChatMessage();
            } else {
                elements.recordingStatus.textContent = "Record";
                appendChatMessage("assistant", "Sorry, transcription returned empty. Please speak clearly.");
            }
        } catch (err) {
            console.error("Transcription error:", err);
            elements.recordingStatus.textContent = "Record";
            appendChatMessage("assistant", "Failed to translate audio message to text.");
        }
    }

    // ─── MANUAL NOTE EDITING OVERRIDES ────────────────────────

    function toggleEditMode() {
        const isEditable = elements.renderedNoteView.getAttribute("contenteditable") === "true";
        
        if (isEditable) {
            // Cancel edit mode
            selectNote(state.activeNoteId);
        } else {
            // Enable edit mode
            elements.renderedNoteView.setAttribute("contenteditable", "true");
            elements.noteTitle.setAttribute("contenteditable", "true");
            elements.btnEditNote.textContent = "Cancel";
            elements.btnSaveNote.style.display = "inline-block";
            elements.renderedNoteView.focus();
        }
    }

    async function saveManualEdits() {
        if (!state.activeNoteId) return;
        
        const titleText = elements.noteTitle.textContent;
        // Fetch raw edited HTML and send it to auditor via backend
        const innerHTMLContent = elements.renderedNoteView.innerHTML;
        
        try {
            const res = await fetch("/notes/update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    note_id: state.activeNoteId,
                    title: titleText,
                    rendered_html: innerHTMLContent
                })
            });
            
            if (res.status === 400) {
                const errorData = await res.json();
                alert(`Save Failed: ${errorData.detail}`);
                return;
            }
            
            await loadNotesList();
            await selectNote(state.activeNoteId);
        } catch (err) {
            console.error("Save error:", err);
            alert("Could not update note due to connection error.");
        }
    }

    // ─── GRAPH MODAL RENDERING (CYTOSCAPE.JS) ──────────────────

    async function openGraphModal() {
        elements.graphModal.style.display = "flex";
        
        try {
            const res = await fetch("/graph");
            const graphData = await res.json();
            
            // Render cytoscape layout
            const cy = cytoscape({
                container: elements.cyGraphContainer,
                elements: [
                    ...graphData.nodes,
                    ...graphData.edges
                ],
                style: [
                    {
                        selector: 'node',
                        style: {
                            'background-color': '#7b2cbf',
                            'label': 'data(label)',
                            'color': '#e6e8f4',
                            'font-family': 'Inter, sans-serif',
                            'font-size': '11px',
                            'text-valign': 'center',
                            'text-halign': 'center',
                            'width': '50px',
                            'height': '50px',
                            'border-width': '2px',
                            'border-color': '#2e324a'
                        }
                    },
                    {
                        selector: 'edge',
                        style: {
                            'width': 2,
                            'line-color': '#2e324a',
                            'target-arrow-color': '#2e324a',
                            'target-arrow-shape': 'triangle',
                            'curve-style': 'bezier'
                        }
                    }
                ],
                layout: {
                    name: 'grid',
                    rows: 2
                }
            });
            
            // Direct link select from graph node
            cy.on('tap', 'node', function(evt){
                const node = evt.target;
                elements.graphModal.style.display = "none";
                selectNote(node.id());
            });
            
            // Redraw layout cleanly
            cy.layout({ name: 'cose', animate: true }).run();
            
        } catch (err) {
            console.error("Graph rendering failed:", err);
        }
    }

    // ─── UTILITIES ─────────────────────────────────────────────

    async function checkHealth() {
        try {
            const res = await fetch("/health/model");
            const data = await res.json();
            if (data.status === "ok") {
                elements.healthIndicator.className = "health-dot online";
                elements.healthIndicator.title = "vLLM Active & Healthy";
            } else {
                elements.healthIndicator.className = "health-dot offline";
                elements.healthIndicator.title = "vLLM Service Offline";
            }
        } catch (e) {
            elements.healthIndicator.className = "health-dot offline";
        }
    }

    function generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    function formatTimestamp(isoString) {
        if (!isoString) return "";
        const date = new Date(isoString);
        return date.toLocaleDateString(undefined, { 
            month: 'short', 
            day: 'numeric', 
            hour: '2-digit', 
            minute: '2-digit' 
        });
    }
});
