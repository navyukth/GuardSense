# GuardSense

GuardSense is a home/property security camera pipeline. It pulls RTSP streams from IP cameras, runs YOLOv8 person detection + ByteTrack tracking on each frame, generates OSNet re-identification embeddings so people can be matched across cameras and over time, persists detections/tracks to a local database, and republishes the annotated video live over WebRTC to a browser.

## Architecture Diagram

<!-- TODO: insert architecture diagram here -->
![Architecture Diagram](architecture-imgs/architecturev1b.png)

## Workflow Diagram

<!-- TODO: insert workflow / data-flow diagram here -->

## Demo Video

<!-- TODO: insert demo video / GIF here -->

---

## How It Works

```
RTSP camera(s)
   │
   ▼
CameraManager (cv2.VideoCapture)
   │
   ▼
GuardSensePipeline (per-camera worker thread)
   │  1. YOLOv8 person detection
   │  2. ByteTrack tracking (stable per-camera track IDs)
   │  3. OSNet embedding of person crops
   │  4. Re-ID matching across cameras/time
   │  5. Persist detections/tracks/embeddings to SQLite
   │  6. Draw annotated frame
   ▼
aiortc WebRTC track → browser (served by streaming/server.py)
```

## Prerequisites

- Python 3.11.15 (pinned via [mise](https://mise.jdx.dev/), see `mise.toml`)
- One or more RTSP-capable IP cameras
- A TURN server (e.g. a [Cloudflare Calls TURN](https://developers.cloudflare.com/calls/turn/) credential, or self-hosted `coturn`) for remote/mobile WebRTC viewers

## Setup

1. **Clone the repo and install the Python version**

   ```bash
   git clone <repo-url>
   cd GuardSense-Main
   mise install
   ```

2. **Create and activate a virtual environment**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

   > `torch`/`torchvision` above install CPU wheels by default. For a CUDA build, install the matching wheel from the [PyTorch install page](https://pytorch.org/get-started/locally/) *before* running `pip install -r requirements.txt`.

4. **Configure environment variables**

   Create a `.env` file in the repo root:

   ```env
   # RTSP camera streams
   RTSP_FRONT_DOOR=rtsp://user:pass@camera-ip:554/path
   RTSP_FRONT_GATE=rtsp://user:pass@camera-ip:554/path
   RTSP_SIDE_GATE=rtsp://user:pass@camera-ip:554/path
   RTSP_TOP=rtsp://user:pass@camera-ip:554/path

   # TURN server (required for WebRTC to work off your local network)
   TURN_USERNAME=your-turn-username
   TURN_CREDENTIAL=your-turn-credential
   ```

5. **Run the server**

   ```bash
   python -m streaming.server
   ```

   This starts the camera → detection → tracking → re-ID pipeline and serves the WebRTC viewer at `http://0.0.0.0:8080`.

6. **(Optional) Expose it publicly**

   GuardSense itself only binds locally. To reach it from the internet, put a tunnel (e.g. Cloudflare Tunnel) or reverse proxy in front of port `8080`.

## Project Structure

| Path | Role |
|---|---|
| `streaming/` | aiohttp + aiortc server, pipeline orchestration |
| `camera/` | RTSP capture management |
| `detection/` | YOLOv8 person detector |
| `tracking/` | ByteTrack adapter |
| `embedding/` | OSNet embedder for re-identification |
| `reid/` | Cross-camera/cross-time identity matching |
| `database/` | SQLite persistence layer |
| `DataClass/` | Shared dataclasses (`Frame`, `Detection`, `Track`, ...) |
