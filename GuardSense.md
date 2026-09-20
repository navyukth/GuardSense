# GuardSense — Project Overview

## What it is

A self-hosted home security camera system. Four RTSP camera feeds (off a
CP Plus DVR) get processed through a person-detection and re-identification
pipeline, and the result — live annotated video, alerts, and a named-person
gallery — is served to a browser over WebRTC. It was originally exposed
through a public domain (with a self-hosted TURN server so it worked from
outside the home network); that public route is currently **switched off
on purpose**, so it's reachable on the home LAN only
(`http://<pi-lan-ip>:8080`). Re-enabling it is one toggle in Nginx Proxy
Manager.

The core idea distinguishing it from "just point a phone app at your
DVR": it doesn't just show you video, it tells you **who** is in the
video. The first time a stranger walks past the camera, GuardSense shows
you "Person #7" and a crop of them. You name them once ("Delivery guy").
Every future sighting — any camera, any time — recognizes them
automatically and the alert says their name instead of a number.

## High-level architecture

```
                    ┌─────────────────────────────────────────┐
                    │           Raspberry Pi 5 (always-on)      │
  4x RTSP cameras ──┼─▶ guardsense-capture                       │
  (CP Plus DVR,     │  (Docker container)                        │
   LAN)             │  - owns the cameras                        │
                     │  - YOLOv8n person detection (NCNN, ARM)    │
                     │  - ByteTrack (per-camera)                  │
                     │  - OSNet re-id embedding                   │
                     │  - live re-id matching                     │
                     │       │                                    │
                     │       │ WebSocket (frames, crops+embeddings,│
                     │       │            alerts, logs)            │
                     │       ▼                                    │
                     │  guardsense-relay                          │
                     │  (Docker container)                        │
                     │  - WebRTC/STUN/TURN termination             │
                     │  - web UI (live feed, People, Logs,         │
                     │    Settings)                                │
                     │  - SQLite identity store + crop images      │
                     │                                             │
                     │  nginx-proxy-manager + coturn                │
                     │  (existing infra, shared with other          │
                     │   services on this Pi)                       │
                     └─────────────────────────────────────────┘
                                        │
                                        │  HTTP on the LAN (public HTTPS domain
                                        │  via NPM exists but is switched off)
                                        ▼
                                    Browser
```

Both `guardsense-capture` and `guardsense-relay` run as Docker containers
on the same Raspberry Pi 5, `restart: unless-stopped`. They talk to each
other over one persistent WebSocket. Nothing needs to be running on a
laptop for the system to work day-to-day — that was a deliberate outcome,
not the starting design (see "Where the pipeline runs" below).

## The pipeline, step by step

1. **Capture** — `camera/CameraManager.py` opens one `cv2.VideoCapture`
   per camera (RTSP, `CAP_FFMPEG` backend) and runs a dedicated thread per
   camera that continuously drains the stream, keeping only the latest
   decoded frame. This decouples camera frame rate from however fast the
   rest of the pipeline can process (see Explanation.md for why this
   matters).

2. **Detection** — once per loop, `detection/yolo_detector.py` grabs the
   *current* latest frame from all 4 cameras and runs **one batched
   YOLOv8n forward pass** across all of them together (not 4 separate
   inference calls). Only the "person" class is kept.

3. **Tracking** — each camera's detections go through its own
   `tracking/bytetrack_adapter.py` instance (ByteTrack). This is
   deliberately **one tracker per camera**, not one shared tracker — a
   person on camera A and a different person on camera B must never be
   assigned the same track ID.

4. **Re-identification embedding** — periodically (every 5th loop, not
   every loop — too expensive to run every frame), active tracks get
   cropped and passed through `embedding/osnet_embedder.py` (OSNet, via
   `torchreid`), pooled across all cameras into one batched extractor
   call. This produces a 512-d feature vector per person crop that
   captures their appearance (clothing, build) in a way that's roughly
   stable across camera angles and lighting.

5. **Live re-identification** — `streaming/identity_matcher.py` polls the
   relay every ~15s for the embeddings of every *named* person, and
   compares each newly-embedded track against them via cosine similarity.
   A match above threshold (default 0.6) means: use that person's name in
   the alert, and tag the crop being sent up as already belonging to them
   (skipping the manual-review queue).

6. **Shipping** — `streaming/capture_node.py` JPEG-encodes the annotated
   frame for each camera and sends it, plus any new crops+embeddings,
   plus alerts, plus log lines, all over one WebSocket to the relay. See
   "Wire protocol" below.

   Embedding runs for every tracked person every cycle (it drives names
   and alerts), but **crops are only sent selectively**: skipped if too
   small, at most one per track every 10 seconds, and capped per track
   (8 for an unrecognised person, 4 for an already-named one). Sending
   every crop used to create thousands of near-identical images per
   person per day (see "Data lifecycle" below).

7. **Relay** — `streaming/relay_server.py` (aiortc + aiohttp) holds the
   latest frame per camera in memory, and whenever a browser connects via
   WebRTC, streams whichever camera is selected. It also serves the web
   UI, and owns `streaming/identity_store.py` (SQLite) — the durable
   record of named people and their crop images.

8. **Browser** — connects over WebRTC (video) plus regular HTTP polling
   (alerts, status, people, logs). coturn provides STUN/TURN, which is what
   lets it work from outside the LAN when the public domain is enabled;
   on the LAN alone it isn't needed.

## Wire protocol (capture → relay)

One persistent WebSocket (`/ws/ingest`, authenticated with a shared
token). Binary messages start with a 1-byte type tag:

- `0` = video frame: `[0][camera_id_len][camera_id][JPEG bytes]`
- `1` = crop + embedding:
  `[1][camera_id_len][camera_id][track_id: 4 bytes][embedding_len: 2 bytes][embedding: float32 * len][person_id: 4 bytes signed, -1 = unmatched][JPEG bytes]`

Text (JSON) messages carry `{"type": "alerts"|"status"|"logs", "data": ...}`.

## The web UI

- **`/`** — live feed, one camera at a time (button per camera), plus an
  alerts panel and a server status panel. The status panel includes Pi
  storage: disk used/free and how much GuardSense's own data takes.
- **`/alerts`** ("History") — alert history for any day, with a camera
  filter and per-person count chips (click a chip to filter). "Unknown" =
  never matched to a named person.
- **`/people`** — the identity review workflow:
  - Unlabeled ByteTrack sightings appear as cards (oldest + newest crop),
    with a dropdown to name them as a new person or merge into an existing
    one. Checkboxes + "select all" allow bulk-deleting piles of
    false-positive sightings.
  - Merging opens a review popup with **two sections** — "Already X's
    crops" and "New crops being added" — so it's clear what's existing and
    what's incoming. Individual crops can be excluded before confirming.
  - Every crop thumbnail opens **full size on click**.
  - "view all →" on a named person opens their **full gallery**: multi-select
    crops, then delete them or move them to another person - or to a
    **brand-new person** ("+ New person…"). This is the fix for two different
    people who ended up under one name.
  - **Long-press and drag** across photos selects several at once, like a
    phone gallery (mouse drag works on desktop).
  - Named people can be deleted (all crops + DB row removed) or merged into
    each other the same way.
- **`/logs`** — a live-updating console of the capture node's log lines
  (camera connects/reconnects, model loads, relay connection state,
  errors), shipped from wherever the pipeline happens to be running.
  Built specifically so you never need SSH access just to see what's
  going on.
- **`/settings`** — admin password, TURN credential, and ingest token,
  masked with a reveal toggle, plus the configured camera list.

All protected by a single admin-password cookie session, except the
`/ws/ingest` and `/api/internal/people-embeddings` endpoints, which use
the shared ingest token instead (the capture node isn't "logged in", it's
a trusted internal service).

## Alerts

Each alert is one JSON object per (camera, track):

```json
{"camera_id": "front_gate", "track_id": 39, "person_id": 2,
 "label": "Dad", "timestamp": "2026-09-19 13:01:34"}
```

- `track_id` — the raw ByteTrack counter. It only ever climbs (it reached
  ~38,000 after 82 hours), so it is **not** a count of people.
- `person_id` — the real identity-store ID, `null` until the track has
  been matched to a named person.
- `label` — the person's name once matched, otherwise `Person #<track_id>`.
  An alert that already fired is updated in place if a later embedding
  cycle matches the track.
- `timestamp` — the container's local time (`TZ=Asia/Kolkata`).

Each alert also carries a stable `id` (UUID) and an epoch `ts`. The relay
**stores every alert in SQLite** (upserting by `id`, so a later name match
updates the same row) and keeps them for 30 days. The main page's alerts
panel shows the latest 100; the **`/alerts`** page (link: "History") shows any
day, filterable by camera and person, and survives restarts and deploys.

## Data lifecycle (what gets kept, what gets deleted)

Left alone, the identity store grew without bound (30,836 crops and a
125 MB database in a few days). It now manages itself:

| Data | Rule |
|---|---|
| Unassigned (never-named) crops + embeddings | Deleted once older than **48 hours**, checked hourly. Rolling, so a fresh sighting is always there to review. |
| Named people's crops | Trimmed to the best **300 per person** (see Explanation.md for how "best" is chosen). |
| Crops sent by the capture node | Filtered at the source — min size, 10 s spacing, per-track cap. |
| Local debug copy of crops on the capture side | Off by default (`SAVE_LOCAL_CROPS=false`). |
| Alerts | Deleted after 30 days (`ALERT_RETENTION_DAYS`). |
| SQLite file | `VACUUM`ed after a large purge, since SQLite never shrinks itself. |

All the numbers are environment variables (`UNASSIGNED_RETENTION_HOURS`,
`PERSON_MAX_CROPS`, `CROP_MIN_INTERVAL`, ...). Deleting crops never
removes a named person, but **when a person has no crops left their stored
embedding is cleared**, so they stop matching new detections (their name is
kept).

Every destructive admin action (delete, merge, move, assign) is written to an
**audit log** - what, when, and from which address - shown on the Logs page
as `AUDIT` lines and stored in the database (`/api/audit`), so "where did my
crops go?" has an answer.

## Deployment (CI/CD)

Pushing to `main` deploys to the Pi5 automatically:
1. A GitHub-hosted job syntax-checks the code.
2. A **self-hosted GitHub Actions runner on the Pi** (outbound connections
   only, no router changes) runs `deploy/deploy.sh`, which copies the changed
   files into the two service folders and rebuilds **only the container(s)
   that changed**, then health-checks them.

`.env` files, `data/` and the model weights stay on the Pi and are never
overwritten. Details and reasoning: `Explanation.md` §22; setup steps:
`PI5_DEPLOYMENT.md`.

## Where the pipeline runs (and why that changed)

The original design intent — laptop does all inference (has a GPU),
Pi5 only does WebRTC/STUN/TURN — is what got rebuilt first. Once it
worked, the actual production decision became "run it entirely on the
Pi5", because:

- The Pi5 is always-on; a laptop isn't.
- With NCNN-accelerated YOLO (see Explanation.md), the Pi5's CPU-only
  throughput (~6 cycles/sec across 4 cameras) is close enough to the
  laptop's (~7.8/sec) that the convenience of "one box, always running"
  wins.

The code doesn't care where it runs — `capture_node.py` just needs
`RELAY_URL`/`RELAY_HTTP_URL` pointed at wherever the relay is (`localhost`
if co-located, a LAN address otherwise), and `INFERENCE_DEVICE`/`YOLO_MODEL`
configured for whatever hardware it's on.

## Repo layout

```
camera/           CameraManager - per-camera RTSP reader thread, auto-reconnect
detection/        YOLODetector - batched YOLOv8 inference (.pt or NCNN export)
tracking/         ByteTrackAdapter - one instance per camera
embedding/        OSNetEmbedder - pooled re-id embedding across cameras
DataClass/        Shared dataclasses (Frame, Detection, Track, Embedding, ...)
streaming/
  capture_node.py       the capture pipeline
  relay_server.py       the relay (WebRTC, routing, ingest websocket)
  identity_store.py     SQLite people/crops store (relay side)
  identity_matcher.py   live re-id matcher (capture side)
  webui.py / people_ui.py / logs_ui.py   the web pages
  export_ncnn.py         one-time NCNN export
  *.Dockerfile, *-docker-compose.yml, *-requirements.txt

deploy/             deploy.sh (Pi5 deploy) + setup-runner.sh (one-time runner install)
.github/workflows/  deploy.yml (push to main -> check -> deploy to the Pi)
PI5_DEPLOYMENT.md   sanitized Pi5 deployment reference
Explanation.md, Topics and tech used.md, challenges faced and bugs we faced.md
```

See `Explanation.md` for the reasoning behind every one of these design
choices, `Topics and tech used.md` for the full technology list, and
`challenges faced and bugs we faced.md` for what actually went wrong
while building this and how it got fixed.
