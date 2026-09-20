# Topics and Technologies Used

A flat reference list — what got used, and the one-line "what it's for"
in this project. See `Explanation.md` for the reasoning behind the
choices, this file is just the index.

## Computer vision / ML

| Topic/Tech | Used for |
|---|---|
| Object detection | Detecting people in each camera frame |
| YOLOv8 (Ultralytics, `yolov8n`) | The detection model itself — nano variant for speed |
| Batched inference | One forward pass across all 4 camera frames per loop, not 4 separate calls |
| Multi-object tracking | Assigning a stable ID to a person across consecutive frames within one camera |
| ByteTrack | The specific tracking algorithm (`ultralytics.trackers.byte_tracker.BYTETracker`) |
| Person re-identification (re-id) | Recognizing the *same* person across different tracks/cameras/time by appearance |
| OSNet (Omni-Scale Network) | The specific re-id embedding model, via `torchreid` |
| Feature embeddings / vector similarity | Representing a person's appearance as a fixed-size vector |
| Cosine similarity | The metric used to compare embeddings for a re-id match |
| NCNN | ARM-optimized neural network inference format/runtime, exported from the YOLO `.pt` model |
| Model export (PyTorch → NCNN) | `ultralytics`'s `.export(format="ncnn")`, via PNNX/ONNX intermediate conversion |
| Non-max suppression (NMS) | Standard detection post-processing (came up specifically in a torch/torchvision version-mismatch bug) |

## Video / streaming

| Topic/Tech | Used for |
|---|---|
| RTSP (Real Time Streaming Protocol) | Pulling live video from the DVR |
| OpenCV (`cv2`) | Video decode (`VideoCapture`), image encoding (`imencode`/`imdecode`), drawing annotations |
| FFmpeg (as OpenCV's backend) | Actual RTSP demuxing/decoding under the hood |
| JPEG encoding | Compressing frames/crops for transport over the ingest websocket |
| WebRTC | Real-time video delivery to the browser (low latency, peer connection) |
| `aiortc` | Python WebRTC implementation used by the relay |
| SDP offer/answer | The WebRTC connection-negotiation handshake, implemented in `relay_server.py`'s `/offer` endpoint and the browser JS |
| ICE (Interactive Connectivity Establishment) | How WebRTC peers find a working network path |
| STUN (Session Traversal Utilities for NAT) | Discovering a client's public-facing address, for NAT traversal |
| TURN (Traversal Using Relays around NAT) | Relaying media when a direct peer connection isn't possible |
| `coturn` | The self-hosted STUN/TURN server implementation |

## Backend / systems

| Topic/Tech | Used for |
|---|---|
| Python `asyncio` | The capture node's and relay's main event loops |
| `aiohttp` | The relay's HTTP server + WebSocket handling |
| Threading (`threading` module) | Per-camera reader threads, decoupled from the async pipeline |
| WebSockets | The persistent capture→relay channel (frames, crops, alerts, status, logs) |
| Binary wire protocols | Custom framed binary messages over the ingest websocket (type byte + length-prefixed fields) |
| `struct` (Python) | Packing/unpacking the binary wire protocol's fixed-size integer fields |
| SQLite | The identity store (named people + their crop/embedding records) |
| Python `logging` module | Structured log output, captured into a ring buffer and shipped to the relay |
| Custom `logging.Handler` | The ring-buffer handler that feeds the Logs page |
| REST-ish JSON APIs | The People/Settings/Logs admin endpoints |
| Cookie-based session auth | The admin web UI login |
| Token-based auth | The ingest websocket and internal embeddings endpoint (separate trust boundary from the admin cookie) |
| Dataclasses | `DataClass/types.py` — `Frame`, `Detection`, `Track`, `Embedding`, etc. |
| Abstract base classes (`abc`) | `Detector`, `Tracker`, `Embedder` interfaces, each with one concrete implementation |

## Infra / deployment

| Topic/Tech | Used for |
|---|---|
| Docker | Containerizing both `guardsense-capture` and `guardsense-relay` |
| Docker Compose | Per-service build + run configuration |
| `network_mode: host` | Required for coturn/WebRTC's wide UDP port range and direct LAN camera access |
| Multi-stage-free Dockerfiles with build-time steps | Baking the NCNN export in at image build time |
| systemd | An intermediate deployment method for the capture node (service unit, auto-restart) before moving to Docker |
| nginx (`nginx-proxy-manager`) | Reverse proxy / TLS termination for the public domain |
| Let's Encrypt | TLS certificates for the public-facing domain |
| DNS / port forwarding | Making the Pi5's self-hosted services reachable from the internet |
| Raspberry Pi 5 / ARM64 (`aarch64`) | The always-on host running the whole system |
| Linux (systemd, Docker on Debian-based OS) | The Pi5's operating environment |
| Windows + PowerShell | The development laptop's environment |
| SSH (key-based auth) | Remote administration of the Pi5 |
| `venv` (Python virtual environments) | Isolated dependency installs, both laptop and Pi5 |
| `pip` / PyPI package indexes | Dependency installation, including the CPU-only PyTorch index specifically |
| Git / GitHub | Version control, including recovering history from a remote nobody had re-fetched in a month |

## Architecture / design patterns

| Topic/Tech | Used for |
|---|---|
| Producer/consumer with a "latest value only" buffer | The threaded camera reader pattern (vs. an unbounded queue) |
| Batching | Both detection and embedding, pooling work across cameras into single calls |
| Adapter pattern | `ByteTrackAdapter`, `OSNetEmbedder` wrapping third-party libraries behind the project's own `Tracker`/`Embedder` interfaces |
| Client/server split with a message-passing boundary | capture node (producer) vs relay (consumer/server), decoupled by a websocket rather than a shared process |
| Polling vs. push | Live re-id matcher polls the relay periodically (pull); frames/alerts/logs are pushed by the capture node (push) — chosen per use case based on staleness tolerance |
| In-memory ring buffers | Log lines and (implicitly) the frame store — bounded memory regardless of runtime duration |
| Exponential-backoff-free fixed retry/reconnect | RTSP reconnect (camera), relay websocket reconnect (capture node) |
| Config via environment variables (`.env`) | All secrets/tunables (RTSP URLs, tokens, thresholds, model paths) kept out of source |

## Data management / retention

| Topic/Tech | Used for |
|---|---|
| Rolling retention policy (time-based purge) | Deleting unnamed crops + embeddings older than 48 h, hourly |
| Quality scoring (Laplacian-variance sharpness + size) | Ranking crops so pruning keeps the good ones |
| Outlier removal via similarity-to-centroid | Dropping likely mis-assigned crops before pruning |
| Farthest-point sampling (max-min diversity selection) | Keeping a *varied* set of reference crops instead of near-duplicates |
| Source-side throttling and filtering | Min size, min interval, per-track caps before a crop is ever sent |
| SQLite schema migration (`PRAGMA table_info` + `ALTER TABLE`) | Adding the `quality` column to an existing database in place |
| SQLite `VACUUM` | Actually shrinking the DB file after large deletes |
| SQLite busy timeout / write contention | Letting ingest writes wait out a maintenance lock |
| Bulk API endpoints | Bulk delete / move so multi-select isn't N separate requests |
| Response caching (TTL) | 60 s cache on the storage-size scan behind `/api/status` |

## Frontend / UI

| Topic/Tech | Used for |
|---|---|
| Vanilla HTML/CSS/JS (no framework) | All pages (`webui.py`, `people_ui.py`, `logs_ui.py`) |
| Lightbox pattern | Full-size crop view on click |
| Touch events (`touchstart/move/end`, non-passive listeners) | Long-press-and-drag multi-select like a phone gallery |
| `document.elementFromPoint()` | Finding which photo is under the finger during a drag |
| Idempotent event binding | Binding drag handlers once per container, swapping callbacks on re-render |
| Polling (`setInterval` + `fetch`) | Live alerts, status, logs, people |

## Ops / debugging

| Topic/Tech | Used for |
|---|---|
| Timezones in containers (`TZ`, `tzdata`) | Fixing timestamps rendered in UTC inside Docker |
| Unix epoch vs rendered local time | Realising the data was right and only the display was wrong |
| Docker layer cache and build cache | Why adding one Dockerfile line forced a 5-minute rebuild; `docker builder prune` |
| Disk forensics (`df`, `du`, `docker system df`, `journalctl --disk-usage`) | Finding that Docker, not app data, was eating the disk |
| Isolation testing | Stopping every process to prove a camera flicker wasn't our code |
| Hypothesis → data → revise | e.g. "stuck track" theory disproved by a per-30-minute crop timeline |
| Nginx Proxy Manager host toggling | Taking the public domain offline without touching other hosts |
| Self-hosted CI/CD options (runner vs webhook vs polling) | Designed but not yet built |

## CI/CD and deployment automation

| Topic/Tech | Used for |
|---|---|
| CI/CD | Push to `main` deploys to the Pi automatically |
| GitHub Actions (workflows, jobs, `needs`, `concurrency`, `workflow_dispatch`, `paths-ignore`) | The pipeline definition in `.github/workflows/deploy.yml` |
| Self-hosted runner (`actions-runner`, `svc.sh`, runner labels) | Running the deploy job on the Pi with outbound-only connections |
| GitHub-hosted runner (`ubuntu-latest`) | The syntax-check job, before code reaches the Pi |
| `python -m compileall`, `bash -n` | Cheap pre-deploy syntax gates |
| Bash scripting (`set -euo pipefail`, `trap`, `mktemp`, functions) | `deploy/deploy.sh`, `deploy/setup-runner.sh` |
| `rsync` (`-c` checksum, `--delete`, `--itemize-changes`, `--dry-run`) | Content-based sync and change detection |
| Change detection → selective rebuilds | Restart only the container whose files changed |
| Health checks after deploy | Relay HTTP probe; container-still-running check |
| Dry-run mode | Previewing a deploy without touching anything |
| `.gitattributes` (`eol=lf`) | Stopping CRLF from breaking scripts on Linux |
| Least privilege for CI | No `pull_request` trigger on a self-hosted runner; secrets kept on the device |
| Rollback strategy (`git revert` + redeploy) | Recovery without blue/green infrastructure |

## Data / API design (added later)

| Topic/Tech | Used for |
|---|---|
| Upsert (`INSERT ... ON CONFLICT DO UPDATE`) | Updating an alert's label when a track is later matched |
| Client-minted stable IDs (UUID) | Keys that survive counter resets across restarts |
| Audit logging | Recording who/what/when for destructive admin actions |
| Time-range queries on epoch timestamps | "Alerts for a given day" in the server's timezone |
| Bounded in-memory structures (`OrderedDict` capped, TTL pruning) | Fixing unbounded per-track memory growth |
| Idempotent/tolerant ingestion | Old capture builds without an alert `id` don't break the relay |
| Resilient reconnect design | Cameras that are down at startup keep being retried |
| Test against a scratch database | Verifying store logic without touching real data |

## Auth / security concepts

| Topic/Tech | Used for |
|---|---|
| Shared-secret token auth | Ingest websocket + internal API, distinct from user login |
| Session cookies (`httponly`, `samesite`) | Admin web UI auth |
| `hmac.compare_digest` | Constant-time password comparison (timing-attack resistant) |
| Credential hygiene / `.gitignore` discipline | Keeping `.env`, `serverpi.md`, the identity DB, and crop images out of version control |
| Least-privilege trust boundaries | The ingest token can push data but can't log into the admin UI; the admin cookie can't authenticate as the capture node |
