
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
from backend.profiles import DriverProfiles
from backend.profiles import MAX_PROFILES


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
driver_profiles = None
profile_enrollment_id = None
ignition_on = True
ignition_off_since = None
occupant_left_behind = False
OCCUPANT_CONFIRM_SECONDS = 8.0


def update_occupant_state():
    global latest_perception, occupant_left_behind
    elapsed = monotonic() - ignition_off_since if ignition_off_since is not None else None
    if (
        profile_enrollment_id is None
        and not ignition_on
        and elapsed is not None
        and elapsed >= OCCUPANT_CONFIRM_SECONDS
        and latest_perception.get("face_detected", False)
    ):
        occupant_left_behind = True
    remaining = max(0, OCCUPANT_CONFIRM_SECONDS - elapsed) if elapsed is not None else None
    latest_perception.update({
        "ignition_on": ignition_on,
        "occupant_left_behind": occupant_left_behind if profile_enrollment_id is None else False,
        "occupant_countdown_seconds": round(remaining, 1) if remaining is not None else None,
        "profile_enrollment_active": profile_enrollment_id is not None,
    })


@app.post("/api/camera/stop")
def camera_stopped():
    global latest_perception, queued_notifications, released_notifications, profile_enrollment_id
    safety_engine.reset()
    profile_enrollment_id = None
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


def get_driver_profiles():
    global driver_profiles
    if driver_profiles is None:
        driver_profiles = DriverProfiles()
    return driver_profiles


class DriverProfileRequest(BaseModel):
    name: str
    age: int
    driving_license: str = ""


@app.get("/api/profiles")
def list_driver_profiles():
    return {"profiles": get_driver_profiles().list_profiles(), "maximum": MAX_PROFILES}


@app.post("/api/profiles")
def create_driver_profile(profile: DriverProfileRequest):
    try:
        created = get_driver_profiles().create_profile(
            profile.name, profile.age, profile.driving_license
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return created


@app.get("/api/profiles/{profile_id}")
def get_driver_profile(profile_id: int):
    profile = get_driver_profiles().get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Driver profile not found")
    return profile


@app.delete("/api/profiles/{profile_id}")
def delete_driver_profile(profile_id: int):
    global profile_enrollment_id
    profiles = get_driver_profiles()
    if not profiles.remove_profile(profile_id):
        raise HTTPException(status_code=404, detail="Driver profile not found")
    if profile_enrollment_id == profile_id:
        profile_enrollment_id = None
        safety_engine.reset()
    return {"status": "removed", "profile_id": profile_id}


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
    if profile_enrollment_id is None and (critical or eyes_clear):
        released_notifications.append(item)
        return {"status": "shown", "notification": item}
    queued_notifications.append(item)
    latest_perception["pending_notifications"] = len(queued_notifications)
    return {"status": "queued", "pending": len(queued_notifications)}


# Lazily initialize the detector when the first frame arrives.
face_perception = None


class CameraFrame(BaseModel):
    image: str
    enrollment_id: int | None = None


@app.post("/api/perception")
def analyze_camera_frame(frame: CameraFrame):
    global face_perception, latest_perception, queued_notifications, released_notifications, profile_enrollment_id

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

    if frame.enrollment_id is not None:
        if profile_enrollment_id != frame.enrollment_id:
            safety_engine.reset()
            queued_notifications.extend(released_notifications)
            released_notifications = []
        profile_enrollment_id = frame.enrollment_id
        latest_perception = safety_engine._state(
            result["face_detected"], None, result["ear"], result["mar"]
        )
        latest_perception.update({
            "eyes_on_road": "Registration",
            "alarm_active": False,
            "alarm_escalation": "none",
            "profile_enrollment_active": True,
        })
    else:
        if profile_enrollment_id is not None:
            safety_engine.reset()
        profile_enrollment_id = None
        latest_perception = safety_engine.update(result)
    update_occupant_state()
    driver_name = "Unknown driver"
    driver_id = None
    driver_recognized = False
    enrollment = None
    if result["face_detected"]:
        points = result["landmarks"]
        image_height, image_width = bgr_image.shape[:2]
        x_values = [point["x"] for point in points]
        y_values = [point["y"] for point in points]
        pad_x = max(8, int((max(x_values) - min(x_values)) * image_width * 0.12))
        pad_y = max(8, int((max(y_values) - min(y_values)) * image_height * 0.12))
        x1 = max(0, int(min(x_values) * image_width) - pad_x)
        x2 = min(image_width, int(max(x_values) * image_width) + pad_x)
        y1 = max(0, int(min(y_values) * image_height) - pad_y)
        y2 = min(image_height, int(max(y_values) * image_height) + pad_y)
        face_crop = bgr_image[y1:y2, x1:x2]
        if face_crop.size:
            profiles = get_driver_profiles()
            if frame.enrollment_id is not None:
                try:
                    enrollment = profiles.add_sample(frame.enrollment_id, face_crop)
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error))
                driver_name = "Profile enrollment"
                if enrollment["complete"]:
                    profile_enrollment_id = None
                    safety_engine.reset()
            else:
                match = profiles.recognize(face_crop)
                if match:
                    driver_name = match["name"]
                    driver_id = match["id"]
                    driver_recognized = True
    latest_perception.update({
        "driver_name": driver_name,
        "driver_id": driver_id,
        "driver_recognized": driver_recognized,
    })
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
        "driver_name": latest_perception["driver_name"],
        "driver_id": latest_perception["driver_id"],
        "driver_recognized": latest_perception["driver_recognized"],
        "enrollment": enrollment,
    }
