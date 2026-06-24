document.addEventListener("DOMContentLoaded", () => {
    const state = {
        sessionId: localStorage.getItem("html_notes_session_id") || generateUUID(),
        mediaRecorder: null,
        audioChunks: [],
        isRecording: false
    };

    localStorage.setItem("html_notes_session_id", state.sessionId);

    const elements = {
        canvasContainer: document.getElementById("canvas-container"),
        chatInput: document.getElementById("chat-input"),
        btnSendMessage: document.getElementById("btn-send-message"),
        btnMic: document.getElementById("btn-mic"),
        recordingStatus: document.getElementById("recording-status"),
        healthIndicator: document.getElementById("health-indicator"),
        welcomeMessage: document.getElementById("welcome-message")
    };

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

    // ─── HISTORY & PERSISTENCE LOGIC ────────────────────────────────
    async function loadHistory() {
        try {
            const res = await fetch(`/session/${state.sessionId}/history`);
            if (!res.ok) return;
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                if (elements.welcomeMessage) elements.welcomeMessage.remove();
                for (const msg of data.messages) {
                    if (msg.role === "user") {
                        // Create a simple user message block
                        const wrapper = document.createElement("div");
                        wrapper.className = "canvas-element user-message";
                        wrapper.innerHTML = `<strong>You:</strong> ${DOMPurify.sanitize(msg.content)}`;
                        elements.canvasContainer.appendChild(wrapper);
                    } else if (msg.role === "assistant" && msg.content !== "[tool-only turn]") {
                        const wrapper = document.createElement("div");
                        wrapper.className = "canvas-element";
                        
                        // Because components are raw HTML and marked output is HTML,
                        // we'll just parse the whole thing and inject it.
                        // We bypass DOMPurify here ONLY for loaded history because it's our own database 
                        // and we want the components to render untouched.
                        
                        // Wait, if it's text, it needs marked. If it's a component, it's already HTML.
                        // Actually, since components are wrapped in <div class="canvas-element rendered-component">
                        // we can just run marked on it. Marked will ignore block HTML!
                        wrapper.innerHTML = marked.parse(msg.content);
                        elements.canvasContainer.appendChild(wrapper);
                        renderDynamicComponents(wrapper);
                    }
                }
                scrollToBottom();
            }
        } catch (err) {
            console.error("Failed to load history:", err);
        }
    }

    // ─── CHAT & RENDERING LOGIC ────────────────────────────────
    async function sendChatMessage() {
        const text = elements.chatInput.value.trim();
        if (!text) return;

        // Clear input
        elements.chatInput.value = "";
        elements.chatInput.style.height = 'auto';

        // Remove welcome message if present
        if (elements.welcomeMessage) {
            elements.welcomeMessage.remove();
        }

        // Show loading indicator on canvas
        const loader = document.createElement("div");
        loader.className = "loading-indicator pulse";
        loader.innerText = "Thinking...";
        elements.canvasContainer.appendChild(loader);
        scrollToBottom();

        try {
            const res = await fetch("/session/message", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: state.sessionId,
                    message: text
                })
            });

            loader.remove();

            if (!res.ok) {
                console.error("Error from API:", await res.text());
                renderError("Failed to process request. See console.");
                return;
            }

            const wrapper = document.createElement("div");
            wrapper.className = "canvas-element";
            
            // Create execution log container
            const execLog = document.createElement("div");
            execLog.className = "execution-log";
            wrapper.appendChild(execLog);
            
            // Create text content container
            const textContent = document.createElement("div");
            textContent.className = "text-content";
            wrapper.appendChild(textContent);

            elements.canvasContainer.appendChild(wrapper);
            scrollToBottom();

            const reader = res.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let done = false;
            let fullHtml = "";

            function addLogStep(text, icon) {
                const step = document.createElement("div");
                step.className = "log-step";
                step.innerHTML = `<span class="step-icon">${icon}</span><span class="step-text">${text}</span>`;
                execLog.appendChild(step);
                scrollToBottom();
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
                                    fullHtml += data.content || "";
                                    textContent.innerHTML = DOMPurify.sanitize(marked.parse(fullHtml));
                                    scrollToBottom();
                                } else if (data.type === "status") {
                                    addLogStep(data.message || "Thinking...", "🧠");
                                } else if (data.type === "done") {
                                    textContent.innerHTML = DOMPurify.sanitize(marked.parse(fullHtml));
                                    addLogStep("Finished generation.", "✨");
                                    execLog.classList.add("completed");
                                } else if (data.type === "component") {
                                    addLogStep("Rendered visual component", "🎨");
                                    const compDiv = document.createElement("div");
                                    compDiv.className = "canvas-element rendered-component";
                                    compDiv.innerHTML = data.content;
                                    // Insert BEFORE the text content container, but AFTER the exec log
                                    wrapper.insertBefore(compDiv, textContent);
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
            textContent.innerHTML = DOMPurify.sanitize(marked.parse(fullHtml));
            renderDynamicComponents(textContent);
            scrollToBottom();

        } catch (err) {
            loader.remove();
            console.error("Network error:", err);
            renderError("Network error. Is the server running?");
        }
    }

    function renderError(msg) {
        const errDiv = document.createElement("div");
        errDiv.className = "canvas-element system-message";
        errDiv.style.color = "var(--danger-color)";
        errDiv.style.marginTop = "1rem";
        errDiv.innerText = msg;
        elements.canvasContainer.appendChild(errDiv);
        scrollToBottom();
    }

    function scrollToBottom() {
        elements.canvasContainer.scrollTop = elements.canvasContainer.scrollHeight;
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
