const localFrontend = ["localhost", "127.0.0.1"].includes(window.location.hostname)
        && ["5500", "5173"].includes(window.location.port);
      const API_URL = window.SMART_COCKPIT_API_URL || (localFrontend ? "http://127.0.0.1:8000" : "");
      let latestEyesOnRoad = "Unknown";
      let currentAlarmActive = false;
      let currentOccupantAlarmActive = false;
      let alarmSilenced = false;
      let pullOverAnnounced = false;
      let occupantAnnounced = false;
      let toastTimer = null;
      const themeAccents = { ocean: "#087e9b", sage: "#2f855a", amber: "#a66700" };
      const playlists = {
        "Focus Flow": ["Midnight Focus", "Deep Work", "Flow State", "Concentration", "Brain Waves", "Quiet Momentum", "Blue Hour", "Soft Signals", "Open Road"],
        "Ambient Calm": ["Gentle Rain", "Ocean Drift", "Soft Light", "Floating", "Stillness", "Cloud Lines", "Slow Horizon", "Quiet Shore", "Evening Air"],
        "Upbeat Energy": ["Neon Rush", "Electric Feel", "Turbo Boost", "Solar Flare", "Adrenaline", "Fast Lane", "Bright Side", "Pulse Drive", "City Lights"],
        "Late Night Drive": ["Midnight City", "Street Lights", "After Hours", "Cruisin'", "Moonlit Road", "Night Signals", "Last Exit", "Silver Skyline", "Distant Stars"],
      };
      const emotionPlaylists = { stressed: "Ambient Calm", tired: "Upbeat Energy" };
      let activePlaylist = "Focus Flow";
      let trackIndex = 0;

      function setText(id, value) {
        document.getElementById(id).textContent = value;
      }

      function showCockpitToast(message, kind) {
        const toast = document.getElementById("commandToast");
        toast.textContent = message;
        toast.classList.remove("emergency", "phone", "track");
        toast.classList.add("active", kind);
        window.clearTimeout(toastTimer);
        toastTimer = window.setTimeout(() => toast.classList.remove("active"), 5000);
      }

      function speakDashboardMessage(message) {
        if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) return;
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(message);
        utterance.lang = "en-IN";
        window.speechSynthesis.speak(utterance);
      }

      function renderNowPlaying() {
        const tracks = playlists[activePlaylist] || playlists["Focus Flow"];
        setText("nowPlaying", tracks[trackIndex % tracks.length]);
      }

      function applyDriverSettings(settings, emotion = "calm") {
        const theme = themeAccents[settings?.theme] ? settings.theme : "ocean";
        document.documentElement.style.setProperty("--cyan", themeAccents[theme]);
        setText("profileTemperatureDisplay", settings ? `${settings.temperature} °C · demo` : "—");
        setText("profilePlaylistDisplay", settings?.playlist || "Focus Flow");
        const emotionPlaylist = emotionPlaylists[emotion];
        const nextPlaylist = emotionPlaylist || (playlists[settings?.playlist] ? settings.playlist : "Focus Flow");
        if (activePlaylist !== nextPlaylist) {
          activePlaylist = nextPlaylist;
          trackIndex = 0;
          renderNowPlaying();
        }
        const emotionTint = { calm: "#0c1b24", stressed: "#241b16", tired: "#171b2c" }[emotion] || "#0c1b24";
        document.documentElement.style.setProperty("--camera-bg", emotionTint);
        setText("emotionStatus", emotion[0].toUpperCase() + emotion.slice(1));
      }

      async function refreshDashboard() {
        const connection = document.getElementById("connection");

        try {
          const response = await fetch(`${API_URL}/api/status`);
          if (!response.ok) throw new Error("API request failed");

          const status = await response.json();

          applyDriverSettings(status.driver_settings, status.emotion || "calm");
          setText("presence", status.presence);
          setText("driverName", status.driver_name || (status.face_detected ? "Unknown driver" : "Awaiting recognition"));
          setText("drowsiness", status.drowsiness);
          setText("distraction", status.distraction);
          setText("eyes", status.eyes_on_road);
          setText("yawnStatus", status.yawning ? "Detected" : "No");
          setText("yawnCount", status.yawn_count_60s ?? 0);
          setText("alarmFrames", `${Math.min(status.alarm_confirmation_frames ?? 0, 5)} / 5 frames`);
          latestEyesOnRoad = status.eyes_on_road;
          if (status.eyes_on_road === "Calibrating") {
            setText("calibrationMessage", `Look straight ahead and hold steady · ${status.attention_calibration_remaining ?? 0} samples remaining`);
          } else if (status.eyes_on_road === "Yes" || status.eyes_on_road === "No") {
            setText("calibrationMessage", "Forward pose calibrated · recalibrate if this estimate seems wrong.");
          }
          setText("apiStatus", "Online");
          setText("cameraFeedStatus", status.camera_connected ? "Live" : status.presence === "Waiting" ? "Waiting" : "Unavailable");
          setText("lastUpdate", new Date().toLocaleTimeString());
          const occupantAlert = Boolean(status.occupant_left_behind);
          currentOccupantAlarmActive = occupantAlert;
          const pullOverTriggered = Boolean(status.pull_over_triggered);
          if (occupantAlert && !occupantAnnounced) {
            const message = "Occupant left behind. Please check the vehicle immediately.";
            showCockpitToast(message, "emergency");
            speakDashboardMessage(message);
            occupantAnnounced = true;
          } else if (!occupantAlert) {
            occupantAnnounced = false;
          }
          if (pullOverTriggered && !pullOverAnnounced) {
            const message = "Emergency pull-over triggered. No traffic camera is connected; roadside clearance is assumed safe for this simulation. Beginning the pull-over now.";
            showCockpitToast(message, "emergency");
            speakDashboardMessage(message);
            pullOverAnnounced = true;
          } else if (!pullOverTriggered) {
            pullOverAnnounced = false;
          }
          setText("alarmTag", occupantAlert
            ? "Occupant alarm: ACTIVE"
            : pullOverTriggered ? "Emergency pull-over: ACTIVE"
            : `Alarm: ${status.alarm_active ? "ACTIVE" : "inactive"}`);
          const ignitionButton = document.getElementById("ignitionToggle");
          ignitionButton.textContent = `Ignition: ${status.ignition_on ? "ON" : "OFF"}`;
          ignitionButton.classList.toggle("ignition-off", !status.ignition_on);
          setText("occupantTimer", status.ignition_on
            ? "Occupant check inactive"
            : occupantAlert
              ? "Occupant detected · alert stays active until they leave or ignition is ON"
              : status.occupant_countdown_seconds == null
                ? "Waiting for a visible occupant"
                : `Confirming occupant · ${Math.ceil(status.occupant_countdown_seconds)}s remaining`);
          const alert = document.getElementById("safetyAlert");
          currentAlarmActive = occupantAlert || status.alarm_active || pullOverTriggered;
          if (currentAlarmActive) {
            alert.textContent = occupantAlert
              ? "OCCUPANT LEFT BEHIND: Presence remained after ignition OFF. Check the vehicle immediately."
              : pullOverTriggered
              ? "EMERGENCY PULL-OVER: Five yawns were confirmed within the last minute. No traffic camera is connected; roadside clearance is assumed safe for this simulation."
              : `DRIVER SAFETY WARNING: ${status.drowsiness === "Drowsy" ? "Drowsiness detected" : "Distraction detected"}. Please focus on the road.`;
            alert.classList.add("active");
            setPullOverSimulation(pullOverTriggered);
            if (!alarmSilenced) startAlarmTone(occupantAlert ? "occupant" : "driver");
          } else {
            alert.classList.remove("active");
            setPullOverSimulation(false);
            alarmSilenced = false;
            stopAlarmTone();
          }
          connection.style.color = "var(--green)";
          connection.innerHTML = '<span class="dot"></span>Backend connected';
        } catch (error) {
          setText("apiStatus", "Offline");
          setText("cameraFeedStatus", "Unavailable");
          connection.style.color = "var(--amber)";
          connection.innerHTML = '<span class="dot"></span>Backend unavailable';
        }
      }

      refreshDashboard();
      setInterval(refreshDashboard, 750);

      let audioContext = null;
      let alarmOscillator = null;
      let alarmGain = null;
      let alarmToneTimer = null;
      let alarmToneKind = null;
      let pullOverFrame = null;
      let pullOverStartedAt = null;
      let pullOverCompleted = false;
      function startAlarmTone(kind = "driver") {
        if (!audioContext) return;
        if (alarmOscillator && alarmToneKind === kind) return;
        if (alarmOscillator) stopAlarmTone();
        alarmOscillator = audioContext.createOscillator();
        alarmGain = audioContext.createGain();
        alarmToneKind = kind;
        alarmOscillator.type = kind === "occupant" ? "triangle" : "square";
        alarmOscillator.frequency.value = kind === "occupant" ? 560 : 880;
        alarmGain.gain.value = kind === "occupant" ? 0.045 : 0.035;
        alarmOscillator.connect(alarmGain).connect(audioContext.destination);
        alarmOscillator.start();
        if (kind === "occupant") {
          let pulseOn = true;
          alarmToneTimer = window.setInterval(() => {
            if (!alarmOscillator || !audioContext) return;
            pulseOn = !pulseOn;
            alarmGain.gain.setTargetAtTime(pulseOn ? 0.05 : 0.002, audioContext.currentTime, 0.025);
          }, 260);
          return;
        }
        let highTone = false;
        alarmToneTimer = window.setInterval(() => {
          if (!alarmOscillator || !audioContext) return;
          highTone = !highTone;
          alarmOscillator.frequency.setTargetAtTime(highTone ? 1050 : 680, audioContext.currentTime, 0.04);
          alarmGain.gain.setTargetAtTime(highTone ? 0.045 : 0.018, audioContext.currentTime, 0.04);
        }, 420);
      }
      function stopAlarmTone() {
        if (!alarmOscillator) return;
        window.clearInterval(alarmToneTimer);
        alarmToneTimer = null;
        alarmOscillator.stop();
        alarmOscillator.disconnect();
        alarmGain.disconnect();
        alarmOscillator = alarmGain = null;
        alarmToneKind = null;
      }

      function setPullOverSimulation(active) {
        const panel = document.getElementById("pullOverSimulation");
        panel.classList.toggle("active", active);
        if (!active) {
          if (pullOverFrame) cancelAnimationFrame(pullOverFrame);
          pullOverFrame = null;
          pullOverStartedAt = null;
          pullOverCompleted = false;
          return;
        }
        if (pullOverFrame || pullOverCompleted) return;
        pullOverStartedAt = performance.now();
        const canvas = document.getElementById("pullOverCanvas");
        const context = canvas.getContext("2d");
        const draw = (now) => {
          const progress = Math.min((now - pullOverStartedAt) / 8000, 1);
          const eased = progress * progress * (3 - 2 * progress);
          const w = canvas.width, h = canvas.height, half = w / 2;
          context.clearRect(0, 0, w, h);
          context.fillStyle = "#f5f8fb";
          context.fillRect(0, 0, w, h);
          context.fillStyle = "#324254";
          context.font = "600 14px Segoe UI, sans-serif";
          context.fillText("DRIVER VIEW", 16, 22);
          context.fillText("TOP-DOWN VIEW", half + 16, 22);
          context.strokeStyle = "#d7e0ea";
          context.beginPath(); context.moveTo(half, 0); context.lineTo(half, h); context.stroke();

          // Driver view: road perspective, shoulder, lane markers, and hood.
          const horizon = 62;
          context.fillStyle = "#eaf3f8"; context.fillRect(0, 30, half, h - 30);
          context.fillStyle = "#d8eadb"; context.fillRect(0, 205, half, h - 205);
          context.fillStyle = "#737e88";
          context.beginPath(); context.moveTo(half * .42, horizon); context.lineTo(half * .62, horizon); context.lineTo(half * .96, h); context.lineTo(half * .08, h); context.closePath(); context.fill();
          context.strokeStyle = "#f6f8fa"; context.lineWidth = 3;
          context.beginPath(); context.moveTo(half * .42, horizon); context.lineTo(half * .25, h); context.moveTo(half * .62, horizon); context.lineTo(half * .82, h); context.stroke();
          context.setLineDash([18, 16]); context.lineDashOffset = -(now / (34 + 30 * eased)) % 34;
          context.beginPath(); context.moveTo(half * .52, horizon); context.lineTo(half * .53, h); context.stroke(); context.setLineDash([]);
          context.fillStyle = "#384b5c";
          context.beginPath(); context.moveTo(half * .32, h); context.lineTo(half * .39, h - 28); context.lineTo(half * .63, h - 28); context.lineTo(half * .72, h); context.closePath(); context.fill();
          context.fillStyle = "#48d9ed";
          context.beginPath(); context.ellipse(half * (.53 + .17 * eased), h - 10, 28, 8, 0, 0, Math.PI * 2); context.fill();

          // Top-down view: car leaves the travel lane and settles on the shoulder.
          const x0 = half + 70, x1 = w - 100, roadTop = 83, roadBottom = 194;
          context.fillStyle = "#d8eadb"; context.fillRect(half, 30, half, h - 30);
          context.fillStyle = "#737e88"; context.fillRect(half, roadTop, half, 89);
          context.fillStyle = "#bdc7cf"; context.fillRect(half, 172, half, 22);
          context.strokeStyle = "#f6f8fa"; context.lineWidth = 2; context.setLineDash([16, 12]);
          context.beginPath(); context.moveTo(half, 139); context.lineTo(w, 139); context.stroke(); context.setLineDash([]);
          context.strokeStyle = "#d39931"; context.beginPath(); context.moveTo(half, roadBottom); context.lineTo(w, roadBottom); context.stroke();
          const carX = x0 + (x1 - x0) * eased;
          const carY = 126 + 57 * eased;
          context.save(); context.translate(carX, carY); context.fillStyle = "#48d9ed";
          context.beginPath(); context.roundRect(-22, -11, 44, 22, 6); context.fill();
          context.fillStyle = "#dce9f0"; context.fillRect(-9, -8, 18, 16); context.restore();
          context.fillStyle = "#324254"; context.font = "12px Segoe UI, sans-serif";
          context.fillText(progress < 1 ? "Slowing down · moving to shoulder" : "Stopped on shoulder", half + 16, 246);
          if (progress < 1) pullOverFrame = requestAnimationFrame(draw);
          else {
            pullOverFrame = null;
            pullOverCompleted = true;
          }
        };
        pullOverFrame = requestAnimationFrame(draw);
      }

      const cameraVideo = document.getElementById("cameraVideo");
      const profileCameraVideo = document.getElementById("profileCameraVideo");
      const cameraPlaceholder = document.getElementById("cameraPlaceholder");
      const profileCameraPlaceholder = document.getElementById("profileCameraPlaceholder");
      const cameraMessage = document.getElementById("cameraMessage");
      const arOverlay = document.getElementById("arOverlay");
      const arContext = arOverlay.getContext("2d");

      let cameraStream = null;
      let analysisRunning = false;
      let analysisInFlight = false;
      let analysisTimer = null;
      let activeEnrollmentId = null;
      let selectedProfileId = null;
      let gestureEnabled = false;
      const frameCanvas = document.getElementById("frameCanvas");
      const frameContext = frameCanvas.getContext("2d", { willReadFrequently: true });

      function drawFaceOverlay(landmarks) {
        const videoRect = cameraVideo.getBoundingClientRect();
        const cameraRect = cameraVideo.parentElement.getBoundingClientRect();
        if (!landmarks?.length || !videoRect.width || !videoRect.height) {
          arOverlay.style.display = "none";
          arContext.clearRect(0, 0, arOverlay.width, arOverlay.height);
          return;
        }
        const scale = window.devicePixelRatio || 1;
        arOverlay.width = Math.round(videoRect.width * scale);
        arOverlay.height = Math.round(videoRect.height * scale);
        arOverlay.style.left = `${videoRect.left - cameraRect.left}px`;
        arOverlay.style.top = `${videoRect.top - cameraRect.top}px`;
        arOverlay.style.width = `${videoRect.width}px`;
        arOverlay.style.height = `${videoRect.height}px`;
        arOverlay.style.display = "block";
        arContext.setTransform(scale, 0, 0, scale, 0, 0);
        arContext.clearRect(0, 0, videoRect.width, videoRect.height);
        const points = landmarks.map(({ x, y }) => ({ x: x * videoRect.width, y: y * videoRect.height }));
        const xs = points.map(point => point.x);
        const ys = points.map(point => point.y);
        const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
        const corner = Math.max(10, Math.min(x1 - x0, y1 - y0) * 0.12);
        arContext.strokeStyle = "#087e9b";
        arContext.lineWidth = 2;
        for (const [x, y, dx, dy] of [[x0,y0,1,1],[x1,y0,-1,1],[x0,y1,1,-1],[x1,y1,-1,-1]]) {
          arContext.beginPath();
          arContext.moveTo(x + dx * corner, y);
          arContext.lineTo(x, y);
          arContext.lineTo(x, y + dy * corner);
          arContext.stroke();
        }
        arContext.fillStyle = "rgba(8,126,155,.72)";
        for (const index of [1, 33, 133, 159, 145, 263, 362, 386, 374, 13, 14, 61, 291]) {
          if (!points[index]) continue;
          arContext.beginPath();
          arContext.arc(points[index].x, points[index].y, 2.2, 0, Math.PI * 2);
          arContext.fill();
        }
      }

      async function analyzeFrame() {
        if (!analysisRunning || analysisInFlight) return;
        if (cameraVideo.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
          analysisInFlight = true;
          try {
            frameCanvas.width = 640;
            frameCanvas.height = Math.round(640 * cameraVideo.videoHeight / cameraVideo.videoWidth);
            frameContext.drawImage(cameraVideo, 0, 0, frameCanvas.width, frameCanvas.height);
            const framePayload = { image: frameCanvas.toDataURL("image/jpeg", 0.72) };
            if (activeEnrollmentId !== null) framePayload.enrollment_id = activeEnrollmentId;
            framePayload.gesture_enabled = gestureEnabled;
            const response = await fetch(`${API_URL}/api/perception`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(framePayload),
            });
            if (!response.ok) throw new Error("Perception request failed");
            const result = await response.json();
            drawFaceOverlay(result.landmarks);
            setText("presence", result.face_detected ? "Detected" : "Not detected");
            setText("driverName", result.driver_name || (result.face_detected ? "Unknown driver" : "Awaiting recognition"));
            if (activeEnrollmentId !== null) {
              if (result.enrollment) {
                setText("profileMessage", `Capturing face samples: ${result.enrollment.sample_count}/${result.enrollment.target}`);
                if (result.enrollment.complete) {
                  activeEnrollmentId = null;
                  setText("profileMessage", "Profile trained. Recognition is active.");
                  document.getElementById("enrollProfile").disabled = false;
                  gestureToggle.disabled = false;
                  loadProfiles();
                  if (selectedProfileId !== null) loadProfileDetails(selectedProfileId);
                }
              } else {
                setText("profileMessage", "Keep your face centered in the camera while samples are captured.");
              }
            }
            setText("earValue", result.ear ?? "—");
            setText("marValue", result.mar ?? "—");
            setText("yawnStatus", result.yawning ? "Detected" : "No");
            setText("yawnCount", result.yawn_count_60s ?? 0);
            const pose = result.head_pose || {};
            setText("poseValue", pose.yaw_degrees == null ? "—" : `${pose.yaw_degrees}° / ${pose.pitch_degrees}°`);
            if (gestureEnabled) {
              setText("gestureStatus", result.gesture ? `Detected: ${result.gesture}` : "Show one hand clearly to the camera.");
              if (result.gesture_triggered) {
                if (result.gesture_action && result.gesture_authorized) {
                  executeCockpitCommand(result.gesture_action, "gestureActionMessage");
                } else {
                  setText("gestureActionMessage", result.gesture_reason || "Gesture ignored: Eyes on road must be Yes to dismiss an alarm.");
                }
              }
            }
            cameraMessage.textContent = result.face_detected ? "Live face analysis active" : "No face detected";
          } catch (error) {
            cameraMessage.textContent = `Analysis unavailable: ${error.message}`;
          } finally {
            analysisInFlight = false;
          }
        }
        if (analysisRunning) {
          window.clearTimeout(analysisTimer);
          analysisTimer = window.setTimeout(analyzeFrame, 350);
        }
      }

      document
        .getElementById("startCamera")
        .addEventListener("click", async () => {
          if (cameraStream) return;

          const AudioContextClass = window.AudioContext || window.webkitAudioContext;
          if (AudioContextClass) {
            audioContext ||= new AudioContextClass();
            if (audioContext.state === "suspended") await audioContext.resume();
          }

          if (!navigator.mediaDevices?.getUserMedia) {
            cameraMessage.textContent =
              "Camera API unavailable. Use localhost.";
            return;
          }

          try {
            cameraMessage.textContent = "Requesting camera permission…";

            cameraStream = await navigator.mediaDevices.getUserMedia({
              video: {
                facingMode: "user",
                width: { ideal: 640 },
                height: { ideal: 480 },
              },
              audio: false,
            });

            cameraVideo.srcObject = cameraStream;
            cameraVideo.style.display = "block";
            cameraPlaceholder.style.display = "none";
            profileCameraVideo.srcObject = cameraStream;
            profileCameraVideo.style.display = "block";
            profileCameraPlaceholder.style.display = "none";
            await cameraVideo.play();
            await profileCameraVideo.play();

            analysisRunning = true;
            window.clearTimeout(analysisTimer);
            analysisTimer = null;
            cameraMessage.textContent = "Camera active; starting analysis…";
            document.getElementById("profileCameraMessage").textContent = "Camera active";
            analyzeFrame();
            document.getElementById("startCamera").disabled = true;
            document.getElementById("profileStartCamera").disabled = true;
          } catch (error) {
            cameraStream = null;
            cameraMessage.textContent =
              error.name === "NotAllowedError"
                ? "Camera permission denied. Allow access in your browser."
                : `Camera error: ${error.message}`;
          }
        });

      document.getElementById("stopCamera").addEventListener("click", () => {
        analysisRunning = false;
        window.clearTimeout(analysisTimer);
        analysisTimer = null;
        gestureEnabled = false;
        gestureToggle.disabled = activeEnrollmentId !== null;
        gestureToggle.textContent = "Start gesture control";
        setText("gestureStatus", "Gesture control is off.");
        if (cameraStream) {
          cameraStream.getTracks().forEach((track) => track.stop());
          cameraStream = null;
        }

        cameraVideo.srcObject = null;
        cameraVideo.style.display = "none";
        arOverlay.style.display = "none";
        cameraPlaceholder.style.display = "block";
        profileCameraVideo.srcObject = null;
        profileCameraVideo.style.display = "none";
        profileCameraPlaceholder.style.display = "grid";
        document.getElementById("profileCameraMessage").textContent = "Camera stopped";
        cameraMessage.textContent = "Camera stopped";
        setText("earValue", "—");
        setText("marValue", "—");
        setText("poseValue", "—");
        fetch(`${API_URL}/api/camera/stop`, { method: "POST" }).catch(() => {});
        stopAlarmTone();
        document.getElementById("startCamera").disabled = false;
        document.getElementById("profileStartCamera").disabled = false;
      });

      document.getElementById("profileStartCamera").addEventListener("click", () => {
        if (cameraStream) return;
        document.getElementById("startCamera").click();
      });
      document.getElementById("profileStopCamera").addEventListener("click", () => {
        if (cameraStream) document.getElementById("stopCamera").click();
      });

      const SpeechRecognitionClass = window.SpeechRecognition || window.webkitSpeechRecognition;
      let voiceRecognition = null;
      let voiceEnabled = false;

      function executeCockpitCommand(command, outputId, recipient = null) {
        if (command === "dismiss alarm") {
          if (currentOccupantAlarmActive) {
            setText(outputId, "Occupant alert remains active until ignition is ON or the person is no longer visible.");
          } else if (currentAlarmActive) {
            alarmSilenced = true;
            stopAlarmTone();
            setText(outputId, "Alarm sound silenced. The visual warning remains until the safety condition clears.");
          } else {
            setText(outputId, "No active alarm to dismiss.");
          }
        } else if (command === "next track") {
          const tracks = playlists[activePlaylist] || playlists["Focus Flow"];
          trackIndex = (trackIndex + 1) % tracks.length;
          renderNowPlaying();
          const message = `Next track is playing: ${tracks[trackIndex]}`;
          setText(outputId, message);
          showCockpitToast(message, "track");
        } else {
          if (!recipient) {
            setText(outputId, "Say “Call” followed by the person's name.");
            return;
          }
          const displayName = recipient.replace(/\b\w/g, (letter) => letter.toUpperCase());
          const message = `Calling ${displayName}`;
          setText(outputId, `${message} · demo only; no phone call was placed.`);
          showCockpitToast(message, "phone");
          speakDashboardMessage(`${message}.`);
        }
      }

      async function processVoiceTranscript(transcript) {
        const spoken = transcript.toLowerCase().replace(/[.,!?]/g, " ").replace(/\s+/g, " ").trim();
        let command = null;
        let recipient = null;
        if (/\bdismiss\s+(the\s+)?alarm\b/.test(spoken)) {
          command = "dismiss alarm";
        } else if (/\bnext\s+(track|song)\b/.test(spoken)) {
          command = "next track";
        } else {
          const callPhrase = spoken.replace(/\b(call|phone\s+call(?:\s+to)?)\s+(?:a\s+)?person\s+/g, "$1 ");
          const callMatch = callPhrase.match(/\b(?:call|phone\s+call(?:\s+to)?)\s+(?:a\s+)?([a-z][a-z'-]*(?:\s+[a-z][a-z'-]*){0,2})\b/);
          if (callMatch) {
            recipient = callMatch[1]
              .replace(/\s+(please|now|for me)$/, "")
              .trim();
            if (!recipient) {
              setText("voiceActionMessage", "Please say a person's name, for example “Call Mohan”.");
              return;
            }
            command = "phone call";
          }
        }
        if (!command) {
          if (/\bphone\s+call\b|\bcall\b/.test(spoken)) {
            setText("voiceActionMessage", "Please say a person's name, for example “Call Mohan”.");
            return;
          }
          setText("voiceActionMessage", `Command not recognized: “${transcript}”`);
          return;
        }

        try {
          const response = await fetch(`${API_URL}/api/voice/authorize`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ command }),
          });
          const authorization = await response.json();
          if (!response.ok) throw new Error(authorization.detail || "Voice command check failed");
          if (!authorization.authorized) {
            setText("voiceActionMessage", `${command} ignored: ${authorization.reason}`);
            return;
          }

          executeCockpitCommand(command, "voiceActionMessage", recipient);
          setText("voiceStatus", `Heard: “${transcript}”`);
        } catch (error) {
          setText("voiceActionMessage", error.message);
        }
      }

      const voiceToggle = document.getElementById("voiceToggle");
      const gestureToggle = document.getElementById("gestureToggle");
      gestureToggle.addEventListener("click", () => {
        if (!cameraStream && !gestureEnabled) {
          setText("gestureStatus", "Start the camera before enabling gesture control.");
          return;
        }
        gestureEnabled = !gestureEnabled;
        gestureToggle.textContent = gestureEnabled ? "Stop gesture control" : "Start gesture control";
        setText("gestureStatus", gestureEnabled
          ? "Gesture control active. Show thumbs up to dismiss an alarm. Eyes on road must be Yes to dismiss."
          : "Gesture control is off.");
        if (!gestureEnabled) setText("gestureActionMessage", "");
      });
      if (SpeechRecognitionClass) {
        voiceRecognition = new SpeechRecognitionClass();
        voiceRecognition.lang = "en-IN";
        voiceRecognition.continuous = true;
        voiceRecognition.interimResults = false;
        voiceRecognition.onresult = (event) => {
          for (let index = event.resultIndex; index < event.results.length; index += 1) {
            if (event.results[index].isFinal) {
              processVoiceTranscript(event.results[index][0].transcript);
            }
          }
        };
        voiceRecognition.onerror = (event) => {
          setText("voiceStatus", event.error === "not-allowed"
            ? "Microphone permission denied."
            : `Voice recognition: ${event.error}`);
          if (event.error === "not-allowed" || event.error === "service-not-allowed") {
            voiceEnabled = false;
            voiceToggle.textContent = "Start voice control";
          }
        };
        voiceRecognition.onend = () => {
          if (voiceEnabled) {
            window.setTimeout(() => {
              if (!voiceEnabled) return;
              try { voiceRecognition.start(); } catch { /* A recognition session may still be closing. */ }
            }, 350);
          } else {
            setText("voiceStatus", "Voice control is off.");
          }
        };
        voiceToggle.addEventListener("click", () => {
          voiceEnabled = !voiceEnabled;
          if (voiceEnabled) {
            try {
              voiceRecognition.start();
              voiceToggle.textContent = "Stop voice control";
              setText("voiceStatus", "Listening for the three supported commands…");
            } catch (error) {
              voiceEnabled = false;
              setText("voiceStatus", `Could not start microphone: ${error.message}`);
            }
          } else {
            voiceRecognition.stop();
            voiceToggle.textContent = "Start voice control";
            setText("voiceStatus", "Voice control is off.");
          }
        });
      } else {
        voiceToggle.disabled = true;
        setText("voiceStatus", "Speech recognition is not supported in this browser.");
      }

      async function loadProfiles() {
        try {
          const response = await fetch(`${API_URL}/api/profiles`);
          if (!response.ok) throw new Error("Could not load profiles");
          const data = await response.json();
          const profiles = data.profiles || [];
          setText("profileCapacity", `${profiles.length} of 8 profiles registered`);
          const list = document.getElementById("profileList");
          list.replaceChildren();
          if (!profiles.length) {
            list.textContent = "No family profiles registered yet.";
            return;
          }
          for (const profile of profiles) {
            const card = document.createElement("button");
            card.type = "button";
            card.dataset.profileId = String(profile.id);
            card.className = `profile-choice${selectedProfileId === profile.id ? " selected" : ""}`;
            const name = document.createElement("strong");
            name.textContent = profile.name;
            const detail = document.createElement("div");
            detail.className = "metric-note";
            detail.textContent = `${profile.sample_count}/40 samples · ${profile.trained ? "trained" : "registration incomplete"}`;
            card.append(name, detail);
            card.addEventListener("click", () => {
              selectedProfileId = profile.id;
              for (const choice of list.querySelectorAll(".profile-choice")) {
                choice.classList.toggle("selected", choice.dataset.profileId === String(profile.id));
              }
              loadProfileDetails(profile.id);
            });
            list.append(card);
          }
        } catch {
          setText("profileCapacity", "Profile service unavailable.");
          document.getElementById("profileList").textContent = "Start the backend to load family profiles.";
        }
      }

      document.getElementById("enrollProfile").addEventListener("click", async () => {
        if (!cameraStream) {
          setText("profileMessage", "Start the camera before enrolling a driver.");
          return;
        }
        const name = document.getElementById("profileName").value.trim();
        const age = Number.parseInt(document.getElementById("profileAge").value, 10);
        const drivingLicense = document.getElementById("profileLicense").value.trim();
        const temperature = Number.parseInt(document.getElementById("profileTemperature").value, 10);
        if (!name) {
          setText("profileMessage", "Enter a driver name first.");
          return;
        }
        if (!Number.isInteger(age) || age < 0 || age > 120 || !Number.isInteger(temperature) || temperature < 16 || temperature > 30) {
          setText("profileMessage", "Enter a valid age (0–120) and demo temperature (16–30 °C).");
          return;
        }
        try {
          const response = await fetch(`${API_URL}/api/profiles`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name,
              age,
              driving_license: drivingLicense,
              theme: document.getElementById("profileTheme").value,
              temperature,
              playlist: document.getElementById("profilePlaylist").value,
            }),
          });
          const profile = await response.json();
          if (!response.ok) throw new Error(profile.detail || "Could not create profile");
          activeEnrollmentId = profile.id;
          selectedProfileId = profile.id;
          gestureEnabled = false;
          gestureToggle.disabled = true;
          gestureToggle.textContent = "Start gesture control";
          setText("gestureStatus", "Gesture control paused during profile registration.");
          document.getElementById("enrollProfile").disabled = true;
          setText("profileMessage", `Capturing face samples: ${profile.sample_count}/40. Keep your face centered.`);
          loadProfiles();
          loadProfileDetails(profile.id);
        } catch (error) {
          setText("profileMessage", error.message);
        }
      });

      async function loadProfileDetails(profileId) {
        const details = document.getElementById("profileDetails");
        try {
          const response = await fetch(`${API_URL}/api/profiles/${profileId}`);
          if (!response.ok) throw new Error("Could not load profile details");
          const profile = await response.json();
          setText("detailName", profile.name);
          setText("detailAge", `Age: ${profile.age ?? "Not provided"}`);
          setText("detailLicense", `Driving licence: ${profile.driving_license || "Not provided"}`);
          setText("detailEnrollment", `${profile.sample_count}/40 samples · ${profile.trained ? "trained for recognition" : "registration incomplete"}`);
          setText("detailPersonalization", `Theme: ${profile.theme} · Demo temperature: ${profile.temperature} °C · Playlist: ${profile.playlist}`);
          const photo = document.getElementById("profilePhoto");
          const fallback = document.getElementById("profilePhotoFallback");
          if (profile.photo) {
            photo.src = profile.photo;
            photo.style.display = "block";
            fallback.style.display = "none";
          } else {
            photo.removeAttribute("src");
            photo.style.display = "none";
            fallback.style.display = "block";
          }
          details.hidden = false;
        } catch (error) {
          setText("profileMessage", error.message);
        }
      }

      document.getElementById("removeProfile").addEventListener("click", async () => {
        if (selectedProfileId === null) return;
        const profileName = document.getElementById("detailName").textContent;
        if (!window.confirm(`Remove ${profileName} and all of their stored face samples?`)) return;
        try {
          const response = await fetch(`${API_URL}/api/profiles/${selectedProfileId}`, { method: "DELETE" });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Could not remove profile");
          if (activeEnrollmentId === selectedProfileId) {
            activeEnrollmentId = null;
            document.getElementById("enrollProfile").disabled = false;
            gestureToggle.disabled = false;
          }
          selectedProfileId = null;
          document.getElementById("profileDetails").hidden = true;
          setText("profileMessage", `${profileName} and their stored face samples were removed.`);
          loadProfiles();
        } catch (error) {
          setText("profileMessage", error.message);
        }
      });

      loadProfiles();

      for (const [buttonId, viewId] of [["dashboardTab", "dashboardView"], ["profilesTab", "profilesView"]]) {
        document.getElementById(buttonId).addEventListener("click", () => {
          const profileViewSelected = viewId === "profilesView";
          document.getElementById("dashboardView").hidden = profileViewSelected;
          document.getElementById("profilesView").hidden = !profileViewSelected;
          document.getElementById("dashboardTab").classList.toggle("active", !profileViewSelected);
          document.getElementById("profilesTab").classList.toggle("active", profileViewSelected);
          document.getElementById("dashboardTab").setAttribute("aria-selected", String(!profileViewSelected));
          document.getElementById("profilesTab").setAttribute("aria-selected", String(profileViewSelected));
          if (profileViewSelected) loadProfiles();
        });
      }

      document.getElementById("ignitionToggle").addEventListener("click", async () => {
        const button = document.getElementById("ignitionToggle");
        const turnOn = button.textContent.includes("OFF");
        try {
          const response = await fetch(`${API_URL}/api/ignition`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ignition_on: turnOn }),
          });
          if (!response.ok) throw new Error("Ignition update failed");
          await refreshDashboard();
        } catch (error) {
          setText("occupantTimer", `Ignition simulation unavailable: ${error.message}`);
        }
      });

      document.getElementById("calibrateAttention").addEventListener("click", async () => {
        if (!cameraStream) {
          setText("calibrationMessage", "Start the camera before calibrating attention.");
          return;
        }
        try {
          const response = await fetch(`${API_URL}/api/attention/recalibrate`, { method: "POST" });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Could not start calibration");
          setText("eyes", "Calibrating");
          setText("calibrationMessage", "Sit in your normal driving position, look straight ahead, and hold steady for about 4 seconds.");
        } catch (error) {
          setText("calibrationMessage", error.message);
        }
      });
