# Smart Cockpit implementation audit

Reviewed source: `stellantis-smart-cockpit-main.zip` (the ZIP supplied in Downloads), its `README.md`, `TEAM_PROMPT.md`, module READMEs, and the current Smart Cockpit workspace. The ZIP is a modular, three-process draft; the current workspace is a FastAPI app with a browser-owned webcam and a Python perception/safety service. The newer user requests take precedence where they differ from the ZIP's original design.

## Original feature coverage

| ZIP/project requirement | Current implementation | Audit result |
|---|---|---|
| Live dashboard and camera | Light HTML dashboard; browser camera frames go to FastAPI | Implemented; dashboard and API smoke-checked |
| Presence, EAR drowsiness, MAR yawns | MediaPipe Face Landmarker; EAR and averaged mouth opening; distinct yawns counted over 60 seconds | Implemented; synthetic state-machine coverage; camera calibration still required |
| Head-pose distraction and eyes-on-road | Forward calibration, gaze/frontality/head pose, confirmation and recovery timing | Implemented; webcam gaze remains an estimate |
| Alarm and pull-over escalation | Alarm after 5 consecutive warning frames; pull-over only after 5 confirmed yawns in a rolling 60 seconds, with 4 consecutive frames required per yawn | Implemented in decision logic; validated with deterministic clock tests; roadside clearance is assumed in the demo |
| Driver recognition and enrollment | OpenCV LBPH, 40 samples, local SQLite, registration UI; recognition is smoothed across recent frames; maximum is 8 profiles per the later user request (the ZIP originally specified 2) | Implemented; SQLite migration and profile CRUD tested; no live enrollment run here |
| Per-driver personalization | Light accent, demo temperature, and playlist saved with a profile and selected after recognition | Implemented; temperature/audio are UI demo values, not vehicle controls |
| Emotion heuristic and ambient feedback | Geometry-based calm/stressed/tired hint; changes ambient tint and demo playlist | Implemented as a rough visual heuristic, not validated emotion inference |
| Voice commands | Dismiss Alarm requires fresh Eyes on Road = Yes; Next Track and Phone Call are not gaze-gated; Next Track selects from a 36-title demo playlist | Implemented in UI/API; browser/microphone required for live check; no audio tracks or phone integration |
| Gesture control | MediaPipe thumbs-up only; stable pose gate; dismissal requires Eyes on Road = Yes | Implemented; other poses are ignored |
| Occupant-left-behind | 9 seconds of continuous, fresh face detection while ignition is off; distinct pulsing tone and spoken alert persist until ignition is turned on or the occupant is no longer visible | Implemented; stale camera state no longer counts as current presence |
| AR face overlay | Browser canvas draws face brackets and landmark points over camera preview | Implemented |
| Phase 5 deployment | Pinned dependencies, Dockerfile, Compose loopback bind, static dashboard serving, host/CORS allowlists, input bounds, biometric database ignored by Git | Starter deployment added; Docker build unavailable here and public deployment/authentication is not configured |

## Mistakes found and corrected

- Gesture policy produced `triggered`, but the dashboard checked a different response field; gesture actions therefore never fired. API now returns the field the UI consumes.
- Stale perception could continue to look like a detected driver and could authorize voice commands or satisfy the occupant timer. The API now expires camera state after 2.5 seconds and treats it as unavailable.
- Camera analysis scheduled another timeout after the stop button, so a queued frame could still be analyzed after stopping. The timer is now cancelled on start/stop and deduplicated.
- The yawn ratio was overly diluted by side measurements and its low threshold produced false yawns on the live webcam (resting MAR median ~0.39). The center opening now has higher weight; the threshold is 0.52 with a 0.42 release threshold, yawn state has hysteresis, and three distinct yawn events update drowsiness; the alarm remains delayed.
- SQLite context-managed connections were not explicitly closed. They now commit/rollback and close reliably, including on Windows.
- Profile recognition returned settings but they were not retained in the shared status object. They now flow through the API to theme, demo temperature, and playlist displays.
- The ZIP's no-auth `http.server` bridge was a local demo, not safe as a public API. The current service stays local by default in Compose and supports explicit host/origin allowlists; it must still be placed behind real authentication before public exposure.

## Verification

- `.venv312\Scripts\python.exe -m unittest discover -s tests -v`: 14 passing state, profile, gesture, and API safety-gate tests.
- `python -m compileall -q backend tests`: passed.
- Inline dashboard JavaScript syntax check: passed.
- `python -m pip check`: passed.
- Local Uvicorn smoke check: `/api/health`, `/`, and `/api/status` succeeded; initial camera state reports `Waiting` and disconnected.
- `git diff --check`: passed.

## Remaining environment-dependent work

- Exercise the yaw/EAR/MAR behavior against your webcam, including a normal blink, several deliberate yawns, and sustained head turns; synthetic tests cannot establish useful real-world thresholds.
- Check voice commands in Chrome with microphone permission and gestures with the intended camera placement.
- Build and run Docker on a machine with Docker installed. Docker is not installed in this environment.
- Before any public deployment, configure HTTPS and authentication/access control, trusted host/origin values, storage backups and retention policy for biometric samples, and deployment monitoring. The current local prototype is not a certified ADAS or vehicle controller.
