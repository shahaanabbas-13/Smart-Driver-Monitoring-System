
import base64
import binascii
import cv2
import numpy as np

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.perception import FacePerception
from backend.safety import SafetyEngine


from datetime import datetime, timezone
from time import monotonic

app = FastAPI(
    title="Smart Cockpit API",
    description="Backend for the Smart Cockpit driver-monitoring dashboard",
    version="0.1.0",
)

# Allow the local frontend to communicate with this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "online",
        "application": "Smart Cockpit",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/status")
def cockpit_status():
    global released_notifications
    update_occupant_state()
    snapshot = dict(latest_perception)
    snapshot["released_notifications"] = list(released_notifications)
    released_notifications = []
    return snapshot


# Latest frame measurements are shared with the dashboard polling endpoint.
safety_engine = SafetyEngine()
latest_perception = safety_engine._state(False, None, None, None)
latest_perception.update({"eyes_on_road": "Unknown", "pending_notifications": 0})
queued_notifications = []
released_notifications = []
ignition_on = True
ignition_off_since = None
occupant_left_behind = False
OCCUPANT_CONFIRM_SECONDS = 8.0


def update_occupant_state():
    global latest_perception, occupant_left_behind
    elapsed = monotonic() - ignition_off_since if ignition_off_since is not None else None
    if (
        not ignition_on
        and elapsed is not None
        and elapsed >= OCCUPANT_CONFIRM_SECONDS
        and latest_perception.get("face_detected", False)
    ):
        occupant_left_behind = True
    remaining = max(0, OCCUPANT_CONFIRM_SECONDS - elapsed) if elapsed is not None else None
    latest_perception.update({
        "ignition_on": ignition_on,
        "occupant_left_behind": occupant_left_behind,
        "occupant_countdown_seconds": round(remaining, 1) if remaining is not None else None,
    })


@app.post("/api/camera/stop")
def camera_stopped():
    global latest_perception, queued_notifications, released_notifications
    safety_engine.reset()
    queued_notifications = []
    released_notifications = []
    latest_perception = safety_engine._state(False, None, None, None)
    latest_perception.update({"presence": "Waiting", "eyes_on_road": "Unknown", "pending_notifications": 0, "released_notifications": []})
    return {"status": "camera stopped"}


class IgnitionRequest(BaseModel):
    ignition_on: bool


@app.post("/api/ignition")
def set_ignition(request: IgnitionRequest):
    global ignition_on, ignition_off_since, occupant_left_behind
    ignition_on = request.ignition_on
    if ignition_on:
        ignition_off_since = None
        occupant_left_behind = False
    elif ignition_off_since is None:
        ignition_off_since = monotonic()
        occupant_left_behind = False
    update_occupant_state()
    return {
        "ignition_on": ignition_on,
        "occupant_left_behind": occupant_left_behind,
        "occupant_countdown_seconds": latest_perception["occupant_countdown_seconds"],
    }


class NotificationRequest(BaseModel):
    title: str
    message: str = ""
    priority: str = "non-critical"


@app.post("/api/notifications")
def submit_notification(notification: NotificationRequest):
    global latest_perception, released_notifications
    item = {"title": notification.title, "message": notification.message}
    critical = notification.priority.lower() == "critical"
    eyes_clear = latest_perception.get("eyes_on_road") == "Yes"
    if critical or eyes_clear:
        released_notifications.append(item)
        return {"status": "shown", "notification": item}
    queued_notifications.append(item)
    latest_perception["pending_notifications"] = len(queued_notifications)
    return {"status": "queued", "pending": len(queued_notifications)}


# Lazily initialize the detector when the first frame arrives.
face_perception = None


class CameraFrame(BaseModel):
    image: str


@app.post("/api/perception")
def analyze_camera_frame(frame: CameraFrame):
    global face_perception, latest_perception, queued_notifications, released_notifications

    # Browser sends a data URL such as data:image/jpeg;base64,...
    encoded = frame.image.split(",", 1)[-1]

    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid image encoding")

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    bgr_image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)

    if bgr_image is None:
        raise HTTPException(status_code=400, detail="Could not decode image")

    rgb_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)

    if face_perception is None:
        face_perception = FacePerception()

    try:
        result = face_perception.analyze_rgb(rgb_image)
    except Exception:
        raise HTTPException(status_code=500, detail="Face analysis failed")

    latest_perception = safety_engine.update(result)
    update_occupant_state()
    latest_perception["pending_notifications"] = len(queued_notifications)
    if latest_perception["eyes_on_road"] == "Yes" and queued_notifications:
        released_notifications.extend(queued_notifications)
        queued_notifications = []
        latest_perception["pending_notifications"] = 0
    # The dashboard currently consumes measurements only; avoid sending the
    # full landmark array back over HTTP on every frame.
    return {
        "face_detected": result["face_detected"],
        "ear": result["ear"],
        "mar": result["mar"],
        "head_pose": result["head_pose"],
    }
