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
        widgetElement.remove();
    },
    isDismissed(id) {
        if (!id) return false;
        return this.getDismissed().includes(id);
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
        wakeWordActive: false
    };

    localStorage.setItem("html_notes_session_id", state.sessionId);

    const elements = {
        liveCanvas: document.getElementById("live-canvas"),
        chatInput: document.getElementById("chat-input"),
        btnSendMessage: document.getElementById("btn-send-message"),
        btnMic: document.getElementById("btn-mic"),
        recordingStatus: document.getElementById("recording-status"),
        healthIndicator: document.getElementById("health-indicator"),
        welcomeMessage: document.getElementById("welcome-message"),
        execLogContainer: document.getElementById("execution-log-container"),
        execLogContent: document.getElementById("execution-log-content"),
        btnToggleLog: document.getElementById("btn-toggle-log"),
        modelSelect: document.getElementById("model-select"),
        btnMute: document.getElementById("btn-mute"),
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

    elements.btnMic.addEventListener("click", toggleRecording);

    // Initial check
    checkHealth();
    fetchModels();
    setInterval(checkHealth, 30000);
    updateMuteButtonUI();

    // Load history
    loadHistory();

    // Mute Button listener
    if (elements.btnMute) {
        elements.btnMute.addEventListener("click", () => {
            state.isMuted = !state.isMuted;
            localStorage.setItem("html_notes_is_muted", state.isMuted);
            updateMuteButtonUI();
            if (state.isMuted) {
                clearSpeechQueue();
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

    function renderContent(textContent, componentHtml) {
        // The text content goes to TTS and Chat History.
        // We ONLY render the HTML component to the live canvas.
        if (componentHtml && componentHtml !== lastRenderedComponentHtml) {
            lastRenderedComponentHtml = componentHtml;
            elements.liveCanvas.innerHTML = DOMPurify.sanitize(componentHtml, {
                ADD_ATTR: ['style', 'class', 'type', 'checked', 'data-component', 'x-data', 'x-show', 'x-model', 'x-text', 'x-bind', 'x-on:click', '@click', 'x-transition', 'x-cloak', 'x-init', 'x-ref', 'x-for', ':class', ':style', 'id', 'placeholder', 'value'],
                FORCE_BODY: true
            });
        }
    }

    // ─── HISTORY & PERSISTENCE LOGIC ────────────────────────────────
    async function loadHistory() {
        try {
            const res = await fetch(`/session/${state.sessionId}/history`);
            if (!res.ok) return;
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                // Populate chat history panel
                elements.chatHistoryMessages.innerHTML = "";
                data.messages.forEach(msg => {
                    if (msg.content !== "[tool-only turn]") {
                        appendChatMessageToHistory(msg.role, msg.content);
                    }
                });

                // Find the last assistant message
                const assistantMessages = data.messages.filter(m => m.role === "assistant" && m.content !== "[tool-only turn]");
                if (assistantMessages.length > 0) {
                    const lastMsg = assistantMessages[assistantMessages.length - 1];
                    // History content is mixed text+HTML, extract only the visual components
                    let temp = document.createElement("div");
                    temp.innerHTML = lastMsg.content;
                    
                    let gridElement = temp.querySelector("#dashboard-grid");
                    
                    if (gridElement) {
                        // Apply filters inside the grid
                        let widgets = gridElement.querySelectorAll(".widget-container, .glass-card, .canvas-element, .rendered-component");
                        widgets.forEach(c => {
                            if (c.textContent.includes("Unknown widget type:")) c.remove();
                            if (c.id && window.WidgetManager && window.WidgetManager.isDismissed(c.id)) c.remove();
                        });
                        
                        elements.liveCanvas.innerHTML = DOMPurify.sanitize(gridElement.outerHTML, {
                            ADD_ATTR: ['style', 'class', 'type', 'checked', 'data-component', 'x-data', 'x-show', 'x-model', 'x-text', 'x-bind', 'x-on:click', '@click', 'x-transition', 'x-cloak', 'x-init', 'x-ref', 'x-for', ':class', ':style', 'id', 'placeholder', 'value'],
                            FORCE_BODY: true
                        });
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
                            elements.liveCanvas.innerHTML = `<div id="dashboard-grid" class="dashboard-grid">${DOMPurify.sanitize(htmlOnly, {
                                ADD_ATTR: ['style', 'class', 'type', 'checked', 'data-component', 'x-data', 'x-show', 'x-model', 'x-text', 'x-bind', 'x-on:click', '@click', 'x-transition', 'x-cloak', 'x-init', 'x-ref', 'x-for', ':class', ':style', 'id', 'placeholder', 'value'],
                                FORCE_BODY: true
                            })}</div>`;
                            renderDynamicComponents(elements.liveCanvas);
                        }
                    }
                }
            }
        } catch (err) {
            console.error("Failed to load history:", err);
        }
    }

    // ─── CHAT & RENDERING LOGIC ────────────────────────────────
    async function sendChatMessage() {
        const text = elements.chatInput.value.trim();
        if (!text) return;

        elements.chatInput.value = "";
        elements.chatInput.style.height = 'auto';

        clearSpeechQueue();
        appendChatMessageToHistory("user", text);
        
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

        // Prepare Execution Log Overlay
        elements.execLogContent.innerHTML = "";
        elements.execLogContainer.style.display = "flex";

        try {
            const res = await fetch("/session/message", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: state.sessionId,
                    message: text,
                    provider: provider,
                    model: model,
                    current_canvas: elements.liveCanvas.innerHTML
                })
            });

            if (!res.ok) {
                console.error("Error from API:", await res.text());
                renderError("Failed to process request. See console.");
                return;
            }

            const reader = res.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let done = false;
            let fullText = "";
            let fullComponentHtml = "";

            function addLogStep(text, icon) {
                const step = document.createElement("div");
                step.className = "log-step";
                step.innerHTML = `<span class="step-icon">${icon}</span><span class="step-text">${text}</span>`;
                elements.execLogContent.appendChild(step);
                elements.execLogContent.scrollTop = elements.execLogContent.scrollHeight;
            }

            addLogStep("Connecting to agent...", "🔗");

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    const chunk = decoder.decode(value, { stream: true });
                    const lines = chunk.split("\n");
                    for (const line of lines) {
                        if (line.startsWith("data: ")) {
                            try {
                                const data = JSON.parse(line.substring(6));
                                if (data.type === "chunk") {
                                    const token = data.content || "";
                                    fullText += token;
                                    renderContent(fullText, fullComponentHtml);
                                    handleIncomingChunk(token);
                                } else if (data.type === "status") {
                                    addLogStep(data.message || "Thinking...", "🧠");
                                } else if (data.type === "done") {
                                    renderContent(fullText, fullComponentHtml);
                                    renderDynamicComponents(elements.liveCanvas);
                                    addLogStep("Finished generation.", "✨");
                                    flushSentenceBuffer();
                                    appendChatMessageToHistory("assistant", fullText + fullComponentHtml);
                                } else if (data.type === "component") {
                                    addLogStep("Rendered visual component", "🎨");
                                    fullComponentHtml = data.content || "";
                                    renderContent(fullText, fullComponentHtml);
                                    scrollToBottom();
                                } else if (data.type === "tool_call") {
                                    addLogStep(`Calling tool: <strong>${data.tool}</strong>...`, "🔧");
                                } else if (data.type === "error") {
                                    addLogStep(`Error: ${data.message}`, "❌");
                                    renderError(data.message || "An error occurred.");
                                }
                            } catch (e) {
                                // ignore parse errors on partial chunks
                            }
                        }
                    }
                }
            }
            
            // Final cleanup
            renderContent(fullText, fullComponentHtml);
            renderDynamicComponents(elements.liveCanvas);

            // Auto-hide log after 3 seconds
            setTimeout(() => {
                elements.execLogContainer.style.display = "none";
            }, 3000);

        } catch (err) {
            console.error("Network error:", err);
            renderError("Network error. Is the server running?");
        }
    }

    function renderError(msg) {
        elements.liveCanvas.innerHTML = `<div class="system-message" style="color: var(--danger-color); margin-top: 1rem;">${msg}</div>`;
    }

    function scrollToBottom() {
        if (elements.liveCanvas) {
            elements.liveCanvas.scrollTop = elements.liveCanvas.scrollHeight;
        }
    }

    function renderDynamicComponents(container) {
        const chartBlocks = container.querySelectorAll('pre code.language-chart');
        chartBlocks.forEach((block) => {
            try {
                const config = JSON.parse(block.innerText);
                
                // Create canvas container
                const canvasContainer = document.createElement('div');
                canvasContainer.className = 'chart-container';
                canvasContainer.style.position = 'relative';
                canvasContainer.style.height = '400px';
                canvasContainer.style.width = '100%';
                canvasContainer.style.marginBottom = '1.5rem';
                
                const canvas = document.createElement('canvas');
                canvasContainer.appendChild(canvas);
                
                // Replace the <pre> tag (parent of code block) with the canvas container
                const pre = block.parentElement;
                pre.parentNode.replaceChild(canvasContainer, pre);
                
                // Initialize Chart.js with dark mode defaults
                Chart.defaults.color = '#c9d1d9';
                Chart.defaults.borderColor = '#30363d';
                
                new Chart(canvas, config);
            } catch (err) {
                console.error("Failed to render chart component:", err);
            }
        });

        // ─── POST-PROCESS WIDGET CONSOLE / CONTROLS ───
        const widgets = container.querySelectorAll('.widget-container');
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
                        <button title="Close Widget" class="close-widget-btn absolute top-3 right-3 text-white/30 hover:text-white/80 opacity-0 group-hover:opacity-100 transition-opacity">
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
            if (id.includes('music') || id.includes('player')) {
                const hasXData = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('musicPlayerWidget');
                const hasPlayButton = widget.querySelector('.material-symbols-outlined');
                
                if (!hasXData || !hasPlayButton || widget.children.length === 0) {
                    let genre = 'jazz';
                    const genreSpan = widget.querySelector('.text-purple-200');
                    if (genreSpan && genreSpan.textContent && genreSpan.textContent.trim() !== 'Radio') {
                        genre = genreSpan.textContent.trim().toLowerCase();
                    }
                    
                    const newWidget = document.createElement('div');
                    newWidget.id = widget.id;
                    newWidget.className = widget.className;
                    newWidget.setAttribute('x-data', `musicPlayerWidget('${genre}', true)`);
                    newWidget.innerHTML = `
                        <!-- Background Blur/Glow effect -->
                        <div class="absolute inset-0 bg-cover bg-center opacity-30 mix-blend-overlay" style="background-image: url('https://images.unsplash.com/photo-1514525253161-7a46d19cd819?q=80&w=600&auto=format&fit=crop')"></div>
                        <div class="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-900/60 to-transparent"></div>
                        
                        <!-- Content Container -->
                        <div class="relative z-10 p-5 flex flex-col h-full justify-between">
                            <!-- Top Bar: Genre / Icon -->
                            <div class="flex justify-between items-start mb-2">
                                <div class="bg-black/40 backdrop-blur-md px-3 py-1 rounded-full border border-white/10 flex items-center gap-1.5 shadow-sm">
                                    <span class="material-symbols-outlined text-[1rem] text-purple-300">graphic_eq</span>
                                    <span class="text-xs font-semibold tracking-wider text-purple-200 uppercase" x-text="genreFilter || 'Radio'"></span>
                                </div>
                                <button title="Close Widget" class="close-widget-btn text-white/50 hover:text-white bg-black/20 hover:bg-black/40 rounded-full p-1.5 backdrop-blur-sm transition-all shadow-sm">
                                    <span class="material-symbols-outlined text-[1rem]">close</span>
                                </button>
                            </div>
                            
                            <!-- Track Info -->
                            <div class="flex items-center gap-4 mt-2">
                                <!-- Album Art Mock -->
                                <div class="w-16 h-16 shrink-0 rounded-2xl bg-gradient-to-tr from-fuchsia-500 to-orange-500 shadow-lg flex items-center justify-center relative overflow-hidden ring-2 ring-white/10">
                                    <div class="absolute inset-0 bg-black/20 transition-opacity" :class="{'opacity-0': !isPlaying, 'animate-pulse': isPlaying}"></div>
                                    <span class="material-symbols-outlined text-3xl text-white relative z-10">album</span>
                                </div>
                                
                                <div class="flex-grow min-w-0 flex flex-col justify-center">
                                    <h4 class="text-lg font-bold text-white truncate leading-tight drop-shadow-md" x-text="currentTrack ? currentTrack.title : 'Searching signals...'"></h4>
                                    <p class="text-sm text-purple-200 truncate mt-0.5 drop-shadow-sm font-medium" x-text="currentTrack ? currentTrack.artist : 'Please wait'"></p>
                                </div>
                            </div>

                            <!-- Progress Bar -->
                            <div class="w-full mt-5 relative group-hover:opacity-100 opacity-80 transition-opacity">
                                <div class="h-1.5 w-full bg-white/10 rounded-full overflow-hidden backdrop-blur-sm shadow-inner">
                                    <div class="h-full bg-gradient-to-r from-purple-400 to-fuchsia-400 rounded-full relative shadow-[0_0_10px_rgba(216,180,254,0.5)] transition-all duration-300" :class="{'w-1/3 animate-[slideRight_10s_linear_infinite]': isPlaying, 'w-0': !isPlaying}"></div>
                                </div>
                            </div>
                            
                            <!-- Controls -->
                            <div class="flex items-center justify-between mt-5 px-1">
                                <button class="text-white/50 hover:text-white transition-colors p-2" title="Shuffle">
                                    <span class="material-symbols-outlined text-xl">shuffle</span>
                                </button>
                                
                                <div class="flex items-center gap-4">
                                    <button @click="nextTrack()" class="w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                                        <span class="material-symbols-outlined">skip_previous</span>
                                    </button>
                                    
                                    <button @click="playPause()" class="w-16 h-16 rounded-[2rem] bg-purple-300 hover:bg-purple-200 text-slate-900 flex items-center justify-center shadow-[0_0_20px_rgba(216,180,254,0.2)] hover:shadow-[0_0_25px_rgba(216,180,254,0.4)] transition-all active:scale-95" :disabled="!currentTrack">
                                        <span class="material-symbols-outlined text-4xl" x-text="isPlaying ? 'pause' : 'play_arrow'" style="font-variation-settings: 'FILL' 1;">play_arrow</span>
                                    </button>
                                    
                                    <button @click="nextTrack()" class="w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center backdrop-blur-md transition-all active:scale-90 shadow-sm" :disabled="!currentTrack">
                                        <span class="material-symbols-outlined">skip_next</span>
                                    </button>
                                </div>
                                
                                <button class="text-white/50 hover:text-white transition-colors p-2" title="Repeat">
                                    <span class="material-symbols-outlined text-xl">repeat</span>
                                </button>
                            </div>
                            
                            <!-- Error State -->
                            <div x-show="error" x-transition class="absolute bottom-2 left-1/2 -translate-x-1/2 bg-red-500/90 text-white text-xs px-3 py-1 rounded-full backdrop-blur-md whitespace-nowrap shadow-lg" x-text="error" style="display: none;"></div>
                        </div>
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
            
            if (closeBtn && !closeBtn.dataset.hasListener) {
                closeBtn.dataset.hasListener = "true";
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
            
            // 2. Ensure clock widget has timezone selector if it's a clock and lacks one
            const isClock = widget.getAttribute('x-data') && widget.getAttribute('x-data').includes('clockWidget');
            if (isClock && !widget.querySelector('select')) {
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
                
                const select = selectContainer.querySelector('select');
                
                const updateSelectFromAlpine = () => {
                    if (window.Alpine) {
                        try {
                            const alpineData = window.Alpine.$data(widget);
                            if (alpineData) {
                                select.value = alpineData.selectedTimezone || 'local';
                                select.addEventListener('change', (e) => {
                                    alpineData.selectedTimezone = e.target.value;
                                    if (typeof alpineData.updateTime === 'function') {
                                        alpineData.updateTime();
                                    }
                                });
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
        });

        // Force Alpine to initialize any uninitialized nodes inside container
        if (window.Alpine && typeof window.Alpine.initTree === 'function') {
            try {
                window.Alpine.initTree(container);
            } catch (err) {
                console.warn("Failed to execute Alpine.initTree:", err);
            }
        }
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
                    const vllmIndex = Array.from(elements.modelSelect.options).findIndex(opt => {
                        try {
                            const val = JSON.parse(opt.value);
                            return val.provider === "vllm";
                        } catch(e) {
                            return false;
                        }
                    });
                    if (vllmIndex !== -1) {
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

    function appendChatMessageToHistory(role, content) {
        if (!elements.chatHistoryMessages) return;
        
        const messageDiv = document.createElement("div");
        messageDiv.className = `chat-message ${role}`;
        
        if (role === "user") {
            messageDiv.textContent = content;
        } else {
            messageDiv.innerHTML = formatAssistantChatBubble(content);
        }
        
        elements.chatHistoryMessages.appendChild(messageDiv);
        
        // Auto-expand on new message
        if (elements.chatHistoryMessages.style.display === "none") {
            elements.chatHistoryMessages.style.display = "flex";
            if (elements.btnToggleHistory) elements.btnToggleHistory.innerText = "▼";
        }
        
        elements.chatHistoryMessages.scrollTop = elements.chatHistoryMessages.scrollHeight;
    }

    function formatAssistantChatBubble(content) {
        let temp = document.createElement("div");
        temp.innerHTML = content;
        
        let components = temp.querySelectorAll(".widget-container, .glass-card, .canvas-element, .rendered-component, .chart-container");
        let hasComponent = components.length > 0;
        
        components.forEach(el => el.remove());
        let cleaned = temp.innerHTML;
        
        let htmlText = "";
        if (cleaned.trim()) {
            htmlText = DOMPurify.sanitize(marked.parse(cleaned), {
                ADD_ATTR: ['style', 'class'],
                FORCE_BODY: true
            });
        }
        
        if (hasComponent) {
            htmlText += `<div class="chat-component-placeholder">🎨 Generated visual component on canvas</div>`;
        }
        
        return htmlText || `<div class="chat-component-placeholder">🎨 Generated visual component on canvas</div>`;
    }

    function handleIncomingChunk(textToken) {
        sentenceBuffer += textToken;
        let match;
        const sentenceRegex = /[^.!?:]+[.!?:]+(?=\s|$)/g;
        
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
        ttsQueue.push(sentence);
        processTTSQueue();
    }

    function clearSpeechQueue() {
        ttsQueue.length = 0;
        if (currentAudio) {
            currentAudio.pause();
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

    function cleanTextForTTS(text) {
        let cleaned = text.replace(/<[^>]*>/g, "");
        cleaned = cleaned.replace(/[\*_#`~]/g, "");
        cleaned = cleaned.replace(/\[([^\]]+)\]\([^\)]+\)/g, "$1");
        cleaned = cleaned.replace(/\s+/g, " ").trim();
        return cleaned;
    }

    async function processTTSQueue() {
        if (isProcessingQueue) return;
        if (ttsQueue.length === 0) {
            hideSpeechOverlay();
            return;
        }
        
        isProcessingQueue = true;
        const sentence = ttsQueue.shift();
        
        try {
            await playSentenceTTS(sentence);
        } catch (err) {
            console.error("Error playing sentence TTS:", err);
        } finally {
            isProcessingQueue = false;
            setTimeout(processTTSQueue, 50);
        }
    }

    function playSentenceTTS(sentence) {
        return new Promise((resolve) => {
            const overlay = document.getElementById("speech-overlay");
            const cleanText = cleanTextForTTS(sentence);
            
            if (!cleanText) {
                resolve();
                return;
            }

            const words = sentence.split(/\s+/).filter(w => w.length > 0);
            
            overlay.innerHTML = "";
            overlay.style.display = "block";
            overlay.classList.remove("sentence-fade-out");
            
            words.forEach((word, index) => {
                const span = document.createElement("span");
                span.className = "word";
                span.textContent = word;
                span.style.animationDelay = `${index * 0.08}s`;
                overlay.appendChild(span);
            });

            if (state.isMuted) {
                const simulationDuration = Math.max(1500, words.length * 300);
                setTimeout(fadeAndFinish, simulationDuration);
            } else {
                fetch("/tts/synthesize", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text: cleanText })
                })
                .then(res => {
                    if (!res.ok) throw new Error("TTS proxy error");
                    return res.blob();
                })
                .then(blob => {
                    const audioUrl = URL.createObjectURL(blob);
                    const audio = new Audio(audioUrl);
                    currentAudio = audio;
                    
                    audio.onended = () => {
                        URL.revokeObjectURL(audioUrl);
                        currentAudio = null;
                        fadeAndFinish();
                    };
                    
                    audio.onerror = (err) => {
                        console.error("Audio playback error:", err);
                        URL.revokeObjectURL(audioUrl);
                        currentAudio = null;
                        const fallbackDuration = Math.max(1500, words.length * 300);
                        setTimeout(fadeAndFinish, fallbackDuration);
                    };
                    
                    audio.play().catch(err => {
                        console.error("Audio play failed:", err);
                        const fallbackDuration = Math.max(1500, words.length * 300);
                        setTimeout(fadeAndFinish, fallbackDuration);
                    });
                })
                .catch(err => {
                    console.error("TTS fetch failed, falling back to silent visualization:", err);
                    const fallbackDuration = Math.max(1500, words.length * 300);
                    setTimeout(fadeAndFinish, fallbackDuration);
                });
            }
            
            function fadeAndFinish() {
                overlay.classList.add("sentence-fade-out");
                setTimeout(() => {
                    overlay.style.display = "none";
                    overlay.innerHTML = "";
                    resolve();
                }, 400);
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
