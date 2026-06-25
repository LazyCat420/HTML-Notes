document.addEventListener("DOMContentLoaded", () => {
    // Configure DOMPurify to allow style attributes for custom layouts
    if (window.DOMPurify) {
        DOMPurify.setConfig({ ADD_ATTR: ['style'] });
    }

    const state = {
        sessionId: localStorage.getItem("html_notes_session_id") || generateUUID(),
        mediaRecorder: null,
        audioChunks: [],
        isRecording: false
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
        modelSelect: document.getElementById("model-select")
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

    // Load history
    loadHistory();

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

    function renderContent(textContent, componentHtml) {
        // Render text through markdown, then append raw component HTML
        let output = "";
        if (textContent) {
            output += DOMPurify.sanitize(marked.parse(textContent), {
                ADD_ATTR: ['style', 'class', 'type', 'checked'],
                FORCE_BODY: true
            });
        }
        if (componentHtml) {
            output += DOMPurify.sanitize(componentHtml, {
                ADD_ATTR: ['style', 'class', 'type', 'checked', 'data-component'],
                FORCE_BODY: true
            });
        }
        elements.liveCanvas.innerHTML = output || "";
    }

    // ─── HISTORY & PERSISTENCE LOGIC ────────────────────────────────
    async function loadHistory() {
        try {
            const res = await fetch(`/session/${state.sessionId}/history`);
            if (!res.ok) return;
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                // Find the last assistant message
                const assistantMessages = data.messages.filter(m => m.role === "assistant" && m.content !== "[tool-only turn]");
                if (assistantMessages.length > 0) {
                    const lastMsg = assistantMessages[assistantMessages.length - 1];
                    // History content is mixed text+HTML, render as raw HTML
                    elements.liveCanvas.innerHTML = DOMPurify.sanitize(lastMsg.content, {
                        ADD_ATTR: ['style', 'class', 'type', 'checked'],
                        FORCE_BODY: true
                    });
                    renderDynamicComponents(elements.liveCanvas);
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
        
        let provider = "vllm-2";
        let model = "cyankiwi/MiniMax-M2.7-AWQ-4bit";
        if (elements.modelSelect && elements.modelSelect.value) {
            try {
                const selected = JSON.parse(elements.modelSelect.value);
                provider = selected.provider;
                model = selected.model;
            } catch (e) {
                console.error("Failed to parse model select value", e);
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
                                    fullText += data.content || "";
                                    renderContent(fullText, fullComponentHtml);
                                } else if (data.type === "status") {
                                    addLogStep(data.message || "Thinking...", "🧠");
                                } else if (data.type === "done") {
                                    renderContent(fullText, fullComponentHtml);
                                    renderDynamicComponents(elements.liveCanvas);
                                    addLogStep("Finished generation.", "✨");
                                } else if (data.type === "component") {
                                    addLogStep("Rendered visual component", "🎨");
                                    fullComponentHtml += data.content || "";
                                    renderContent(fullText, fullComponentHtml);
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
                    elements.modelSelect.options[0].selected = true;
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
});
