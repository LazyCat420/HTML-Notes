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
            const loadingIndicator = document.createElement("div");
            loadingIndicator.className = "streaming-indicator pulse";
            loadingIndicator.innerText = "Generating...";
            wrapper.appendChild(loadingIndicator);
            elements.canvasContainer.appendChild(wrapper);
            scrollToBottom();

            const reader = res.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let done = false;
            let fullHtml = "";

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
                                    wrapper.innerHTML = DOMPurify.sanitize(fullHtml) + '<div class="streaming-indicator pulse">Generating...</div>';
                                    scrollToBottom();
                                } else if (data.type === "status") {
                                    const indicator = wrapper.querySelector(".streaming-indicator");
                                    if (indicator) {
                                        indicator.innerText = "Working: " + (data.message || "Using tools...");
                                    }
                                } else if (data.type === "done") {
                                    wrapper.innerHTML = DOMPurify.sanitize(fullHtml);
                                }
                            } catch (e) {
                                // ignore parse errors on partial chunks
                            }
                        }
                    }
                }
            }
            
            // Final cleanup
            wrapper.innerHTML = DOMPurify.sanitize(fullHtml);
            scrollToBottom();

        } catch (err) {
            loader.remove();
            console.error("Network error:", err);
            renderError("Network error. Is the server running?");
        }
    }

    // Keep renderError for other uses
    function renderError(msg) {
        const errDiv = document.createElement("div");
        errDiv.className = "canvas-element system-message";
        errDiv.style.color = "var(--danger-color)";
        errDiv.style.marginTop = "1rem";
        errDiv.innerText = msg;
        elements.canvasContainer.appendChild(errDiv);
        scrollToBottom();
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
