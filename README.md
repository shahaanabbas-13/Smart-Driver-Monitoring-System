# Smart Cockpit

Local driver-monitoring demo built with FastAPI, OpenCV, MediaPipe, and a browser dashboard. The browser owns webcam permission and sends reduced JPEG frames to the local Python service; the server does not open a webcam device itself.

## Run locally (Windows PowerShell)

Use Python 3.12. From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

In a second terminal, serve the dashboard:

```powershell
python -m http.server 5500 --directory frontend
```

Open <http://localhost:5500>. Allow camera access. Calibrate forward attention by looking at the camera while sitting in the normal driving position. Voice recognition depends on browser support; gesture recognition requires the camera to be running.

The MediaPipe task files in `data/` are required model assets and are intentionally kept in source control. Driver profiles and face samples are written locally to `data/driver_profiles.sqlite3`; that database is ignored by Git.

## Run with Docker

```powershell
docker compose up --build
```

Open <http://localhost:8000>. The Compose port is bound to loopback and persists profile data in `data/`.

For a public HTTPS deployment, place this service behind a properly configured HTTPS reverse proxy and authentication/access control. Do not expose the unauthenticated development API directly to the public Internet. Browser camera access requires a secure context (HTTPS, or localhost for local development). For a separately hosted frontend, set `window.SMART_COCKPIT_API_URL` before the application script to the HTTPS API origin, set `SMART_COCKPIT_ALLOWED_ORIGINS` to a comma-separated list of exact trusted site origins, and set `SMART_COCKPIT_ALLOWED_HOSTS` to the API hostnames.

Family member profiles store their theme accent, demo temperature, and demo playlist alongside face samples. The temperature and playlists are UI demo values; this application has no integration that controls a real vehicle climate or audio system. Emotion colors are a simple facial-landmark heuristic and should not be interpreted as a reliable assessment of a driver's mental state.

## Verify

Run the state-machine unit tests:

```powershell
python -m unittest discover -s tests -v
```

Check the API health endpoint at <http://127.0.0.1:8000/api/health>.

## Safety limits

This is a prototype, not a certified driver-assistance system. Eyes-on-road and drowsiness are approximate webcam estimates and require testing in the intended camera position, lighting, and vehicle. The pull-over feature is only a visual simulation; it has no vehicle-control connection.
