
import base64
import binascii
import cv2
import numpy as np
import os
from collections import Counter, deque
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.perception import FacePerception
from backend.safety import SafetyEngine
from backend.profiles import DriverProfiles
from backend.profiles import MAX_PROFILES
from backend.gestures import GestureActionGate, HandGestureDetector


from datetime import datetime, timezone
from time import monotonic

app = FastAPI(
    title="Smart Cockpit API",
    description="Backend for the Smart Cockpit driver-monitoring dashboard",
    version="0.1.0",
)

# Allow the local frontend to communicate with this API.
default_origins = {
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}
configured_origins = {
    origin.strip()
    for origin in os.getenv("SMART_COCKPIT_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(default_origins | configured_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    host.strip()
    for host in os.getenv(
        "SMART_COCKPIT_ALLOWED_HOSTS",
        "localhost,127.0.0.1,testserver,shahaanabbas.top,*.shahaanabbas.top",
    ).split(",")
    if host.strip()
])


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
    fresh = perception_is_fresh()
    snapshot["camera_connected"] = fresh
    snapshot["perception_age_seconds"] = (
        round(monotonic() - last_frame_received_at, 2)
        if last_frame_received_at is not None else None
    )
    if not fresh:
        snapshot["presence"] = "Waiting" if last_frame_received_at is None else "Camera unavailable"
        snapshot["face_detected"] = False
        snapshot["eyes_on_road"] = "Unknown"
        snapshot["yawning"] = False
        snapshot["alarm_active"] = bool(snapshot.get("occupant_left_behind"))
        if not snapshot["alarm_active"]:
            snapshot["alarm_escalation"] = "none"
    snapshot["released_notifications"] = list(released_notifications)
    released_notifications = []
    return snapshot


@app.post("/api/attention/recalibrate")
def recalibrate_attention():
    if profile_enrollment_id is not None:
        raise HTTPException(status_code=409, detail="Finish profile registration before calibrating attention")
    safety_engine.recalibrate_attention()
    latest_perception.update({
        "eyes_on_road": "Calibrating",
        "distraction": "Inactive",
        "attention_calibration_remaining": safety_engine.ATTENTION_CALIBRATION_SAMPLES,
    })
    if latest_perception.get("drowsiness") != "Drowsy":
        latest_perception.update({"alarm_active": False, "alarm_escalation": "none"})
    return {
        "status": "calibrating",
        "required_samples": safety_engine.ATTENTION_CALIBRATION_SAMPLES,
        "instruction": "Sit in your normal driving position and look straight ahead.",
    }


# Latest frame measurements are shared with the dashboard polling endpoint.
safety_engine = SafetyEngine()
latest_perception = safety_engine._state(False, None, None, None)
latest_perception.update({"eyes_on_road": "Unknown", "pending_notifications": 0})
last_frame_received_at = None
queued_notifications = []
released_notifications = []
driver_profiles = None
profile_enrollment_id = None
hand_gesture_detector = None
gesture_action_gate = GestureActionGate()
ignition_on = True
ignition_off_since = None
occupant_left_behind = False
driver_identity_history = deque(maxlen=12)
OCCUPANT_CONFIRM_SECONDS = 8.0
PERCEPTION_STALE_AFTER_SECONDS = 2.5


def perception_is_fresh():
    return (
        last_frame_received_at is not None
        and monotonic() - last_frame_received_at <= PERCEPTION_STALE_AFTER_SECONDS
    )


def update_occupant_state():
    global latest_perception, occupant_left_behind
    elapsed = monotonic() - ignition_off_since if ignition_off_since is not None else None
    if (
        profile_enrollment_id is None
        and not ignition_on
        and elapsed is not None
        and elapsed >= OCCUPANT_CONFIRM_SECONDS
        and latest_perception.get("face_detected", False)
        and perception_is_fresh()
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
    global latest_perception, queued_notifications, released_notifications, profile_enrollment_id, last_frame_received_at, occupant_left_behind, driver_identity_history
    safety_engine.reset()
    last_frame_received_at = None
    profile_enrollment_id = None
    occupant_left_behind = False
    driver_identity_history.clear()
    gesture_action_gate.reset()
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
    name: str = Field(min_length=1, max_length=80)
    age: int = Field(ge=0, le=120)
    driving_license: str = Field(default="", max_length=120)
    theme: Literal["ocean", "sage", "amber"] = "ocean"
    temperature: int = Field(default=22, ge=16, le=30)
    playlist: Literal["Focus Flow", "Ambient Calm", "Upbeat Energy", "Late Night Drive"] = "Focus Flow"


@app.get("/api/profiles")
def list_driver_profiles():
    return {"profiles": get_driver_profiles().list_profiles(), "maximum": MAX_PROFILES}


@app.post("/api/profiles")
def create_driver_profile(profile: DriverProfileRequest):
    try:
        created = get_driver_profiles().create_profile(
            profile.name, profile.age, profile.driving_license,
            profile.theme, profile.temperature, profile.playlist,
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
    global profile_enrollment_id, driver_identity_history, latest_perception
    profiles = get_driver_profiles()
    if not profiles.remove_profile(profile_id):
        raise HTTPException(status_code=404, detail="Driver profile not found")
    if profile_enrollment_id == profile_id:
        profile_enrollment_id = None
        safety_engine.reset()
    driver_identity_history = deque(
        (item for item in driver_identity_history if item[0] != profile_id), maxlen=12
    )
    if latest_perception.get("driver_id") == profile_id:
        latest_perception.update({
            "driver_id": None,
            "driver_name": "Unknown driver",
            "driver_recognized": False,
            "driver_settings": None,
        })
    return {"status": "removed", "profile_id": profile_id}


class NotificationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(default="", max_length=500)
    priority: Literal["critical", "non-critical"] = "non-critical"


class VoiceCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=40)


@app.post("/api/voice/authorize")
def authorize_voice_command(request: VoiceCommandRequest):
    command = request.command.strip().lower()
    allowed_commands = {"dismiss alarm", "next track", "phone call"}
    if command not in allowed_commands:
        raise HTTPException(status_code=400, detail="Unsupported voice command")
    eyes_on_road = perception_is_fresh() and latest_perception.get("eyes_on_road") == "Yes"
    authorized = eyes_on_road and profile_enrollment_id is None
    return {
        "command": command,
        "authorized": authorized,
        "reason": "Eyes on road confirmed" if authorized else "Eyes on road is not confirmed",
    }


@app.post("/api/notifications")
def submit_notification(notification: NotificationRequest):
    global latest_perception, released_notifications
    item = {"title": notification.title, "message": notification.message}
    critical = notification.priority.lower() == "critical"
    eyes_clear = (
        perception_is_fresh()
        and latest_perception.get("eyes_on_road") == "Yes"
        and latest_perception.get("face_detected", False)
    )
    if profile_enrollment_id is None and (critical or eyes_clear):
        released_notifications.append(item)
        return {"status": "shown", "notification": item}
    queued_notifications.append(item)
    latest_perception["pending_notifications"] = len(queued_notifications)
    return {"status": "queued", "pending": len(queued_notifications)}


# Lazily initialize the detector when the first frame arrives.
face_perception = None


class CameraFrame(BaseModel):
    image: str = Field(min_length=100, max_length=2_000_000)
    enrollment_id: int | None = None
    gesture_enabled: bool = False


@app.post("/api/perception")
def analyze_camera_frame(frame: CameraFrame):
    global face_perception, latest_perception, queued_notifications, released_notifications, profile_enrollment_id, hand_gesture_detector, last_frame_received_at

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
    last_frame_received_at = monotonic()

    if frame.enrollment_id is not None:
        if profile_enrollment_id != frame.enrollment_id:
            safety_engine.reset()
            driver_identity_history.clear()
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
            driver_identity_history.clear()
        profile_enrollment_id = None
        latest_perception = safety_engine.update(result)
    update_occupant_state()
    gesture_result = {"gesture": None, "action": None, "authorized": False}
    if frame.gesture_enabled and profile_enrollment_id is None:
        if hand_gesture_detector is None:
            hand_gesture_detector = HandGestureDetector()
        try:
            detected_gesture = hand_gesture_detector.detect(rgb_image)
        except Exception:
            raise HTTPException(status_code=500, detail="Hand gesture analysis failed")
        gesture_result = gesture_action_gate.process(
            detected_gesture,
            latest_perception.get("eyes_on_road") == "Yes",
        )
    else:
        gesture_action_gate.reset()
    driver_name = "Unknown driver"
    driver_id = None
    driver_recognized = False
    driver_settings = None
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
                driver_identity_history.clear()
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
                    driver_identity_history.append((match["id"], match))
                else:
                    driver_identity_history.append((None, None))
                labels = [label for label, _profile in driver_identity_history if label is not None]
                if len(driver_identity_history) >= 5 and labels:
                    profile_id, votes = Counter(labels).most_common(1)[0]
                    if votes / len(driver_identity_history) >= 0.6:
                        accepted = next(
                            sample for sample_id, sample in reversed(driver_identity_history)
                            if sample_id == profile_id and sample is not None
                        )
                        driver_name = accepted["name"]
                        driver_id = accepted["id"]
                        driver_recognized = True
                        driver_settings = {
                            "theme": accepted["theme"],
                            "temperature": accepted["temperature"],
                            "playlist": accepted["playlist"],
                        }
    else:
        driver_identity_history.clear()
    latest_perception.update({
        "driver_name": driver_name,
        "driver_id": driver_id,
        "driver_recognized": driver_recognized,
        "driver_settings": driver_settings,
        "camera_connected": True,
    })
    latest_perception["pending_notifications"] = len(queued_notifications)
    if (latest_perception["eyes_on_road"] == "Yes"
            and latest_perception.get("face_detected", False)
            and queued_notifications):
        released_notifications.extend(queued_notifications)
        queued_notifications = []
        latest_perception["pending_notifications"] = 0
    # Face landmarks are sent only to the local dashboard's AR overlay.
    return {
        "face_detected": result["face_detected"],
        "ear": result["ear"],
        "mar": result["mar"],
        "emotion": result.get("emotion", "calm"),
        "yawning": latest_perception.get("yawning", False),
        "yawn_count_60s": latest_perception.get("yawn_count_60s", 0),
        "landmarks": result["landmarks"],
        "gaze": result.get("gaze"),
        "frontal_score": result.get("frontal_score"),
        "head_pose": result["head_pose"],
        "driver_name": latest_perception["driver_name"],
        "driver_id": latest_perception["driver_id"],
        "driver_recognized": latest_perception["driver_recognized"],
        "driver_settings": latest_perception.get("driver_settings"),
        "enrollment": enrollment,
        "gesture": gesture_result["gesture"],
        "gesture_action": gesture_result["action"],
        "gesture_authorized": gesture_result["authorized"],
        "gesture_triggered": gesture_result["triggered"],
    }


# Serve the dashboard and API from one origin in container deployments. In
# local development, frontend/index.html can still be served on port 5500.
app.mount(
    "/",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True),
    name="frontend",
)
