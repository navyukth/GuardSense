# GuardSense

Home security camera pipeline: RTSP cameras → YOLOv8 person detection →
per-camera ByteTrack → OSNet re-identification → a WebRTC live feed with
person naming/merging and live re-id, all backed by a self-hosted
STUN/TURN relay.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │              Pi5 (always-on)             │
  RTSP cameras ──┐  │                                           │
  (CP Plus DVR)  │  │  ┌─────────────────┐   ┌───────────────┐  │
                 ├──┼─▶│ guardsense-      │   │ nginx-proxy-  │  │
                 │  │  │ capture          │   │ manager       │  │◀── browser
                 │  │  │ (YOLO+ByteTrack  │   │ + coturn      │  │    (https://
                 │  │  │  +OSNet, Docker) │──▶│ (STUN/TURN)   │  │    guard.
                 │  │  └────────┬─────────┘   └───────────────┘  │    navyukth.
                 │  │           │ ws (frames, crops+embeddings,   │    tech)
                 │  │           │    alerts, logs)                 │
                 │  │           ▼                                 │
                 │  │  ┌─────────────────┐                        │
                 │  │  │ guardsense-relay │  SQLite (identities,   │
                 │  │  │ (WebRTC/aiortc,  │  crop images) + the    │
                 │  │  │  Docker)         │  web UI                │
                 │  │  └─────────────────┘                        │
                    └─────────────────────────────────────────┘
```

- **guardsense-capture**: owns the cameras, runs one batched YOLOv8n pass
  per loop across all feeds, tracks each camera with its own ByteTrack
  instance (track IDs never cross feeds), periodically re-embeds active
  tracks with OSNet, matches them against known people (live re-id) and
  ships annotated frames + crops + alerts + logs to the relay over one
  WebSocket.
- **guardsense-relay**: terminates WebRTC to the browser using a
  self-hosted coturn STUN/TURN server, serves the web UI (live feed,
  People, Logs, Settings), and owns the SQLite identity store + crop
  images. Runs no model inference itself.

Both currently run as Docker containers on the same Pi5
(`network_mode: host`, since capture needs LAN access to the DVR and both
need unrestricted access to the WebRTC/TURN port range). The pipeline can
also run on a separate machine (e.g. a laptop with more CPU headroom) by
pointing `RELAY_URL`/`RELAY_HTTP_URL` in `.env` at the relay's address
instead of `localhost` - see [`serverpi.md`](serverpi.md) (gitignored,
has real credentials) for the full Pi5 infra reference.

## Repo layout

```
camera/           CameraManager - per-camera RTSP reader thread, auto-reconnect
detection/        YOLODetector - batched YOLOv8 inference (.pt or NCNN export)
tracking/         ByteTrackAdapter - one instance per camera
embedding/        OSNetEmbedder - pooled re-id embedding across cameras
DataClass/        Shared dataclasses (Frame, Detection, Track, Embedding, ...)
streaming/
  capture_node.py       the capture pipeline (camera → detect → track → embed → ship)
  relay_server.py       the relay (WebRTC, web UI routing, ingest websocket)
  identity_store.py     SQLite-backed people/crops store (relay side)
  identity_matcher.py   live re-id matcher (capture side, polls relay for embeddings)
  webui.py / people_ui.py / logs_ui.py   the web pages
  export_ncnn.py         one-time NCNN export for faster ARM inference
  *.Dockerfile, *-docker-compose.yml, *-requirements.txt   per-service Docker builds
```

## Running it

### 1. Configure

Copy `.env.example` to `.env` and fill in your camera RTSP URLs, TURN
credentials, and tokens. Key settings:

| Var | Meaning |
|---|---|
| `RTSP_FRONT_DOOR` etc. | RTSP URL per camera |
| `INFERENCE_DEVICE` | `cuda` (NVIDIA GPU) or `cpu` |
| `YOLO_IMGSZ` | Lower = faster, less accurate on small/far people |
| `YOLO_MODEL` | `yolov8n.pt`, or an NCNN export dir for ARM speedups |
| `RELAY_URL` / `RELAY_HTTP_URL` | Where the capture node sends frames/pulls known embeddings |
| `RELAY_INGEST_TOKEN` | Shared secret between capture and relay |
| `ADMIN_PASSWORD` | Web UI login |
| `REID_MATCH_THRESHOLD` | Cosine similarity cutoff for live re-id matches |

### 2. Run locally (dev)

```bash
python -m venv .venv
.venv/Scripts/activate      # or source .venv/bin/activate on Linux/Mac
pip install -r requirements.txt
python -m streaming.relay_server    # in one terminal
python -m streaming.capture_node    # in another
```

Open `http://localhost:8080`, log in with `ADMIN_PASSWORD`.

### 3. Deploy on the Pi5 (production)

Both services build from `streaming/`:

```bash
# relay
cd ~/npm/guardsense-relay && docker compose up -d --build

# capture (bakes an NCNN export of yolov8n.pt at build time - see export_ncnn.py)
cd ~/npm/guardsense-capture && docker compose up -d --build
```

For ARM boards (Raspberry Pi 5, etc.), set `YOLO_MODEL=yolov8n_ncnn_model`
and run `python -m streaming.export_ncnn <imgsz>` once (or let the Docker
build do it) - NCNN meaningfully outperforms plain PyTorch on ARM CPUs.

## Web UI

- **`/`** - live feed, one camera at a time
- **`/people`** - review unlabeled ByteTrack sightings, name them or merge
  into an existing person (with a crop-review popup to drop outliers
  before merging), delete individual crops or whole people
- **`/logs`** - live capture-node log stream
- **`/settings`** - admin password / TURN credential / ingest token
  (masked, reveal on click), configured cameras

## Notes

- `serverpi.md` has real infrastructure credentials (Pi SSH, TURN, API
  keys) - gitignored, never commit it.
- `crops/`, `crops_relay/`, `*.db` (the identity store + crop images) are
  gitignored - they fill up with real footage of people and never belong
  in the repo.
