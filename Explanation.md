# Explanation — Concepts, Design Choices, and Why

This file exists to answer "why did we do it this way and not some other
way" for every non-obvious decision in the project. Read `GuardSense.md`
first for the overview; this is the deep dive.

---

## 1. Why RTSP + OpenCV instead of a vendor SDK

The CP Plus DVR exposes RTSP streams in a Dahua-compatible URL scheme
(`rtsp://user:pass@host:554/cam/realmonitor?channel=N&subtype=M`).
`cv2.VideoCapture(url, cv2.CAP_FFMPEG)` decodes it directly — no vendor
SDK, no proprietary client. This makes the camera layer hardware-agnostic:
any RTSP-capable camera/DVR works, not just this specific brand.

`subtype=1` requests the DVR's **sub-stream** (lower resolution) rather
than the main stream — cheaper to decode and plenty for detection at
320-640px input size anyway.

## 2. The threaded camera reader (and why it exists)

**Problem hit:** the live feed had ~1 minute of lag. Video was real, just
stale by a minute.

**Root cause:** `cv2.VideoCapture` with the FFMPEG backend keeps an
internal decode buffer. `cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)` is supposed
to limit it to 1 frame, but this property is **not reliably honored** by
the FFMPEG backend for network streams — the underlying demuxer manages
its own buffering that OpenCV doesn't fully control. If the consumer
(`cap.read()`) is called slower than the camera produces frames — which
is exactly what happens when a CPU-bound YOLO pass across 4 cameras takes
longer than the cameras' frame interval — that buffer backs up. Every
`read()` returns the *next unread* frame, not the *newest* frame, so the
backlog just keeps growing. After a minute of the pipeline running slower
than the camera's frame rate, `read()` is returning a frame from a minute
ago.

**Fix (`camera/CameraManager.py`):** a dedicated thread per camera does
nothing but call `cap.read()` in a tight loop and store *only the latest*
decoded frame (with a lock). The main pipeline's `get_frame()` just reads
whatever's currently stored — instantly, no blocking. This decouples
"how fast can we drain the RTSP stream" (limited only by decode speed,
which is fast) from "how fast can we run YOLO+ByteTrack+OSNet" (much
slower). The pipeline naturally skips frames instead of falling behind —
correct behavior for a live security feed (you want the *current* moment,
not every historical frame).

**Reconnect logic:** the same thread also handles RTSP drops. After 10
consecutive failed reads, it releases the connection, waits 2s, and
reopens it. Without this, a single dropped RTSP connection means that
camera's thread spins forever calling `read()` on a dead handle, and the
feed freezes permanently until the whole process restarts. (This was a
real bug — see `challenges faced and bugs we faced.md`.)

## 3. Why "native batch" detection instead of 4 separate detectors

The requirement was explicit: one YOLO model instance, one forward pass
per loop covering all 4 cameras — not 4 independent detection threads
each running its own YOLO call. `YOLODetector.detect_batch()` collects
the current frame from every camera into a list and passes the whole
list to `model.predict(source=images, ...)` in one call. Ultralytics
batches the images internally for one GPU/CPU forward pass.

**Why this matters:** on CPU (no GPU), model inference has fixed
per-call overhead beyond the actual compute (Python/C++ call overhead,
memory allocation, etc). Batching 4 images into 1 call amortizes that
overhead once instead of 4 times, and lets the underlying BLAS/NCNN
library use whatever internal parallelism it has across the batch,
instead of 4 serialized single-image calls.

## 4. Why tracking is per-camera, not shared

ByteTrack assigns sequential integer track IDs starting from 1 within
its own state. If one `BYTETracker` instance were shared across all 4
cameras, "track #3" could mean a completely different person depending
on which camera's detections happened to get assigned that number first
— IDs would collide across feeds. `capture_node.py` keeps a
`dict[camera_id -> ByteTrackAdapter]`, one tracker per camera, each with
its own independent ID space. A `session_key` of `"{camera_id}:{track_id}"`
is used everywhere downstream (alerts, crop grouping, the People UI) so
"front_door:3" and "front_gate:3" are never confused.

**Trade-off accepted:** if the same person walks from one camera's view
to another's, they get a *new* track ID on the second camera — ByteTrack
has no cross-camera identity concept. This is exactly what the OSNet
re-id layer exists to solve, at a higher level (matching by *appearance*,
not by tracker state).

## 5. Why OSNet for re-identification (and not just relying on ByteTrack)

ByteTrack solves *short-term* tracking: "this bounding box in frame N and
this bounding box in frame N+1 are probably the same physical object,
based on motion/IoU". It has zero concept of *who* that object is, and
its ID resets constantly (new track every time someone leaves and
re-enters frame, every camera restart, every time the tracker loses
them for too many frames).

OSNet (Omni-Scale Network, via `torchreid`) is a person re-identification
model: given a cropped image of a person, it outputs a feature vector
such that the *same person* photographed at different times/angles/cameras
produces *similar* vectors (by cosine similarity), and *different people*
produce dissimilar ones. This is what makes "Person #7 today is the same
Person #12 from yesterday, on a different camera" possible — something
pure tracking can never do.

**Why not run it every frame:** OSNet inference has real cost, and
running it once per person per few seconds is more than enough — a
person's appearance doesn't change frame-to-frame. `EMBEDDING_INTERVAL = 5`
(every 5th loop) balances "catch people reasonably quickly" against "don't
spend CPU re-embedding the same person 30 times a second". Batched the
same way as detection: `OSNetEmbedder.extract_batch()` pools every crop
across all cameras into one extractor call.

## 6. The live re-identification design (identity_matcher.py)

**The requirement:** merging crops together in the People UI should mean
something for *future* detections too — if you've already told the
system "this is Avyukth", the next time Avyukth walks past any camera,
the alert should say "Avyukth", not "Person #14".

**Why this couldn't just be server-side:** the embeddings (ground truth
for "who is who") live in the relay's SQLite DB, but *alerts* are
generated on the capture side, the instant a new track appears — before
there's any round-trip to the relay. So the capture node needs its own
local, low-latency copy of "known people -> embedding" to compare
against in real time.

**Design:** `IdentityMatcher` polls `GET /api/internal/people-embeddings`
(a relay endpoint, token-authenticated like the ingest websocket, not
cookie-authenticated like the admin UI) every 15s, caches the list
in memory, and does a simple cosine-similarity nearest-neighbor search
against it whenever a new embedding is computed. Above a threshold
(default 0.6, tunable via `REID_MATCH_THRESHOLD`), it's a match.

**Why cosine similarity and not, say, Euclidean distance or a trained
classifier:** cosine similarity is the standard metric for re-id
embeddings specifically because these networks are trained to make
*direction* in embedding space meaningful, not magnitude — two vectors
pointing the same way represent the same identity regardless of overall
activation scale. It's also O(1) to compute per comparison and needs no
training/calibration step, which matters for a home system with a
handful of known people, not a dataset large enough to train a classifier
on.

**What a match does:**
1. The alert's label gets set to the matched name — including
   *retroactively* updating an alert that already fired with a generic
   label, once a later embedding cycle confirms the match (see
   "alerts_by_track" in capture_node.py — alerts are keyed by
   `(camera_id, track_id)`, not just appended, specifically so this
   in-place update is possible).
2. The crop being sent to the relay carries the matched `person_id`
   instead of `-1`, so it's inserted directly assigned to that person
   (and their mean embedding is recomputed to include it) instead of
   landing in the unlabeled review queue.

## 7. Why the identity store is SQLite, on the relay, not the capture side

Two constraints pointed the same direction:
- The **browser** only ever talks to the relay (it's the public-facing
  box) — so whatever backs the People UI has to be reachable from there.
- The DB needs to **persist independent of which machine is running
  capture** — if the pipeline moves from laptop to Pi5 (which it
  eventually did), the identity data shouldn't move with it or reset.

SQLite specifically (not Postgres/MySQL) because this is a single-writer,
low-concurrency, single-machine use case — one relay process, occasional
admin actions through the UI, no need for a separate DB server process.
`sqlite3` is stdlib, zero extra deployment complexity, and the whole
identity store (rows + one crop-image folder) is trivially backed up as
files.

**Schema:**
- `persons(id, name, embedding BLOB, created_at)` — `embedding` is the
  running mean of every crop's embedding, recomputed on every
  assign/merge/delete
- `crops(id, person_id NULL, session_key, camera_id, track_id, filename,
  embedding BLOB, created_at)` — `person_id IS NULL` means "unlabeled,
  awaiting review"

Embeddings are stored as pickled numpy arrays (`pickle.dumps`) in a BLOB
column — simplest option for a fixed-size float vector that's never
queried by value (only ever loaded whole and compared in Python).

## 8. Why merging is "review both sides, then commit" instead of instant

**Requirement:** merging two people (or assigning an unlabeled sighting
to an existing person) should let you look at *all* the crops involved
first and exclude outliers before it's final — because re-id matching
(both the live matcher and a human eyeballing thumbnails) isn't perfect,
and a wrongly-merged crop is hard to untangle later (the person's mean
embedding would need recomputing anyway, but visually it's confusing).

**Implementation:** `POST /api/people/merge` and the "assign to existing
person" path both accept an `exclude_crop_ids` list. The frontend
(`people_ui.py`) fetches every crop from both sides, renders them in a
modal, lets you click to toggle individual crops as excluded (they get
deleted, not just unlinked — they're outliers, not just unlabeled), and
only sends the merge request once you confirm.

## 9. NCNN — what it is and why it was added

**Problem:** the Pi5's ARM CPU running plain PyTorch YOLO inference
managed ~2.2 batched-detection cycles/sec across 4 cameras — noticeably
laggy compared to the laptop's ~7.8/sec (different, faster x86 CPU).

**What NCNN is:** a lightweight neural network inference library (built
by Tencent) specifically optimized for mobile/ARM CPUs — no GPU
required, minimal dependencies, hand-tuned kernels for ARM NEON
instructions. Ultralytics can export a `.pt` model directly to NCNN's
format (`.param` + `.bin` files) via `model.export(format="ncnn")`, and
then load/run it through the *same* `YOLO()`/`.predict()` API — it's a
drop-in backend swap, not a rewrite.

**Result:** ~6.0 cycles/sec on the same Pi5 — roughly a 2.7x speedup,
same model, same accuracy, just a CPU-architecture-appropriate execution
backend instead of PyTorch's general-purpose one.

**Why not do this for OSNet too:** OSNet only runs every 5th loop
(`EMBEDDING_INTERVAL`), so it's a much smaller fraction of total CPU
time than YOLO (which runs every loop). Converting YOLO alone captured
most of the available speedup; OSNet could get the same treatment later
if it becomes the bottleneck.

**Why imgsz matters alongside this:** `YOLO_IMGSZ` (640 default, dropped
to 320-416 for CPU deployments) controls the input resolution YOLO
resizes to before inference. Lower resolution = less compute = faster,
at the cost of missing small/far-away people. This and NCNN are
independent, stackable levers — imgsz for "how much work per inference",
NCNN for "how fast is each unit of work".

## 10. Why capture and relay are separate processes/containers at all

Alternative considered: one all-in-one process (this is literally what
the recovered-but-superseded `streaming/server.py` from the archived
August implementation did). Rejected for the final design because:

- **Deployment flexibility** — capture (needs a GPU or decent CPU, needs
  LAN access to cameras) and relay (needs to be internet-reachable,
  needs the TURN/coturn setup) have different requirements. Splitting
  them means capture can run wherever has the best hardware, without the
  relay's public-facing surface needing to also host camera credentials
  or heavy ML dependencies.
- **Independent restart/failure domains** — a capture-side crash (camera
  driver issue, model error) doesn't take down the browser-facing web UI
  or drop existing WebRTC viewers; the relay just shows "No live feed"
  for that camera and keeps running.
- **Matches the existing Pi5 infra pattern** — every other service on
  this Pi5 (TagDrop, a WhatsApp bot, nginx-proxy-manager, coturn) is
  already a single-purpose Docker container behind the same nginx setup.
  `guardsense-capture` and `guardsense-relay` following the same pattern
  means one mental model for the whole box, one `docker compose up -d
  --build` workflow, one place to check `docker ps`.

## 11. Why `network_mode: host` for these containers specifically

Docker's default bridge networking NATs container ports, which breaks
two things here:
- **coturn/WebRTC** need a wide UDP port range (49152-49352) reachable
  directly — NAT-ing that range through Docker's bridge defeats the
  purpose of STUN/TURN, which exists to solve NAT traversal in the first
  place.
- **guardsense-capture** needs to reach the camera DVR (LAN) and the
  relay (`localhost`, when co-located) — host networking is simplest
  here since there's no need to expose any port *from* this container to
  anything else.

Every other service on the Pi5 that *doesn't* need this (TagDrop,
WhatsApp bot, nginx itself) stays on the Docker bridge network
(`npm_network`), reachable by container name. The rule of thumb used
throughout: host-networked containers get reached by the Pi's raw LAN
IP in nginx configs (no container-name DNS on the bridge network),
bridge-networked containers get reached by container name.

## 12. Why CPU-only PyTorch has to be installed explicitly in Docker

`pip install torch` (no index specified) pulls PyPI's default wheel,
which on Linux includes CUDA/cuDNN as dependencies **even on hardware
with no NVIDIA GPU at all** (like this ARM Pi5). That's ~1GB+ of
completely unused downloads and disk space. The fix:
`pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu`
— PyTorch publishes CPU-only wheels for ARM64 Linux at this index
specifically. Installing `torch` **and** `torchvision` together from the
same index in the same command matters — installing them separately (or
in separate steps) risks pip picking mismatched builds, which breaks at
runtime with `RuntimeError: operator torchvision::nms does not exist`
(see bugs doc).

## 13. Why logging (not `print()`) for the Logs page

Needed a way to see what the capture pipeline is doing without SSHing
into whichever machine happens to be running it. `print()` output only
goes to that process's own stdout/stderr — invisible from a browser.
The fix: route everything through Python's `logging` module, with a
custom `logging.Handler` (`_RingBufferLogHandler`) that queues formatted
records in memory. The main loop periodically drains that queue and
ships new lines to the relay over the same ingest websocket (a `"logs"`
message type, alongside `"alerts"` and `"status"`), which stores them
in a capped deque and serves them via `/api/logs` for the `/logs` page
to poll.

This also gave a uniform way for `CameraManager` (which runs its own
threads) to log through the same pipe — it uses
`logging.getLogger("guardsense.camera")`, a child of the `"guardsense"`
logger the handler is attached to, so its reconnect/failure messages
show up in the same stream automatically via logger propagation.

## 14. Docker containers run in UTC unless told otherwise (the timezone bug)

A container has no timezone of its own; with nothing set it renders
local time as **UTC**, regardless of the host's setting. Python's
`time.strftime()` / `datetime.fromtimestamp()` (used for alert timestamps,
log lines) read the OS timezone, so every timestamp came out 5h30m behind
IST. Nothing was wrong with the pipeline — it just *looked* like alerts had
stopped 5.5 hours ago, because the newest alert's clock time was 07:00 when
it was really 12:30.

Two things are needed for a fix, and the second is easy to miss:
1. `TZ=Asia/Kolkata` in each service's `environment:` in compose.
2. The `tzdata` package inside the image. Debian slim doesn't ship it, and
   without `/usr/share/zoneinfo` the `TZ` variable silently does nothing.

Note the unix epoch (`time.time()`) is timezone-independent — only the
*rendering* was wrong. That's why the crop `created_at` values were fine and
only human-readable strings were off. Side effect: adding a line to the
Dockerfile above the pip layers invalidates Docker's layer cache from that
point down, forcing the torch install to redo (a 5-minute rebuild instead
of 30 seconds).

## 15. Alert fields: `track_id` vs `person_id`

The alert payload originally had one field, `person_id`, which was really
the raw ByteTrack track ID — a holdover from before re-identification
existed, when a track was the closest thing to a person. After 82 hours it
read `38545`, which looked like "38,545 people". Two different concepts,
so two fields now: `track_id` (raw counter, meaningless as a count) and
`person_id` (the real identity-store ID, `null` until matched). Naming a
field after what you *hoped* it would become, rather than what it is,
is a good way to mislead your future self.

## 16. False positives: confidence and resolution trade off

Bikes, shadows and reflections were being detected as "person" and each
became its own noisy track. Two settings interact:

- `YOLO_CONFIDENCE` was 0.3 (keep anything with 30%+ confidence). Raised to
  0.5 — fewer false positives, at the cost of missing some low-confidence
  real people.
- `YOLO_IMGSZ` had been lowered to 320 for speed. Fewer pixels means
  ambiguous shapes (a bike frame, a coat on a hook) are harder to tell from
  people, so the low resolution *causes* some of these mistakes.

The class filter (`classes=[0]`, COCO "person") means a bike is never
labelled "bicycle" and rejected — it can only get through by being
misclassified as a person. So the levers are confidence and resolution;
there is no "exclude bikes" switch. The bulk-delete on the People page
handles whatever still leaks through.

## 17. Crop volume: throttle at the source, prune later, purge the rest

**The problem:** embedding runs about once a second per tracked person,
and every embedding shipped a crop. One person standing in view for a
minute meant ~60 near-identical images. Over a few days: 30,836 crops, a
125 MB SQLite file, ~12,700 crops for one person — mostly redundant, and
slower to browse and to average.

**Layered fix, cheapest layer first:**

1. **At the source (`capture_node.py`) — don't create the junk.**
   Embedding still runs for every track (it drives names and alerts), but a
   crop is only sent if it is big enough (`CROP_MIN_HEIGHT/WIDTH`), at least
   `CROP_MIN_INTERVAL` (10 s) after the previous one for that track, and
   under a per-track cap (8 for an unrecognised person, 4 for one already
   matched — we already hold plenty of references for them). The
   per-track bookkeeping dict is pruned every ~600 loops so it can't grow
   forever.
2. **Rolling purge of unnamed crops (relay, hourly).** Anything with no
   person that's older than 48 h is deleted — row, embedding and image.
   *Rolling* instead of "wipe everything every 2 days" so a sighting from
   ten minutes ago is never deleted before you've had a chance to name it.
3. **Quality-based pruning of named people (relay, hourly).** Over 300
   crops? Keep the best 300, chosen in three steps:
   - **Drop outliers:** crops whose embedding is far from the person's own
     centroid (cosine similarity below ~0.45) are usually mis-assigned or a
     different person.
   - **Drop junk:** blurry/tiny crops (quality score below 0.1), as long as
     enough remain.
   - **Keep a varied set:** *farthest-point sampling* over the embeddings,
     weighted by quality — repeatedly pick the crop most different from
     everything already picked. The result covers different angles, poses
     and lighting instead of 300 copies of the same second of video.

**Quality score:** `0.6 × sharpness + 0.4 × size`, where sharpness is the
variance of the Laplacian (a standard focus measure — blur removes edges
and drives it toward zero) capped at 150, and size is pixel area capped at
20,000. It's a heuristic tuned by eye, not measured against ground truth —
worth revisiting if recognition quality drops.

**Why prune after the fact and not only filter at ingest?** Both. Ingest
filtering stops new junk; pruning fixes what already exists and keeps a
person's set from drifting toward whichever crops happened to arrive most.

**Other pieces this needed:**
- *Schema migration in place.* `quality` was added to the `crops` table
  after first deploy, so `init_db()` checks `PRAGMA table_info` and runs
  `ALTER TABLE ... ADD COLUMN` if it's missing. Old rows get scored lazily
  by reading their image files the first time a person is pruned.
- *SQLite never shrinks its file on its own,* so after a big purge the
  relay runs `VACUUM` (125 MB → 3.7 MB here).
- *Write contention.* The retention job and live crop ingest both write to
  the same SQLite file, and `VACUUM` takes a brief exclusive lock. So
  connections use a 60 s busy timeout, and the ingest handler catches a
  failed crop write instead of letting it kill the websocket that also
  carries the live video.
- *Duplicate local copy.* The capture container was also saving every
  embedded crop to its own disk (184 MB) — off by default now
  (`SAVE_LOCAL_CROPS=false`).

## 18. People-page interaction design

- **Lightbox.** One shared full-size overlay; every thumbnail carries a
  `data-zoom` URL. Clicking the enlarged view closes it.
- **Two-section merge review.** The merge popup is fed two lists — the
  surviving identity's crops and the incoming ones — so "already X" and
  "being added" are visually separate, instead of one mixed grid where you
  can't tell which is which.
- **Bulk operations need bulk endpoints.** Bulk-deleting 329 sightings as
  329 requests would be slow and half-fail on error, so there are
  `bulk-delete` and `move` endpoints that take id lists, and embeddings are
  recomputed *once per affected person* at the end, not once per crop.
- **Moving a crop moves its embedding.** The embedding is stored on the
  crop row itself, so reassigning `person_id` carries it along; the
  person's mean embedding is then recomputed for both the source and the
  destination. If a person loses *all* their crops, their embedding is
  cleared (see §26) so they stop matching.
- **Long-press-and-drag selection** (like a phone gallery). On touch: a
  350 ms press starts selection mode, the first photo's state decides
  whether the drag selects or deselects, and `elementFromPoint()` finds the
  photo under the finger each `touchmove`. Details that matter: the
  `touchmove` listener must be non-passive so it can `preventDefault()` and
  stop the page scrolling mid-drag; a press that moves more than ~10 px
  before the timer fires is treated as a scroll, not a selection; the
  browser's own long-press image menu is suppressed; and a click that
  follows a drag is swallowed for ~400 ms so it doesn't also open the
  lightbox. On desktop the same helper handles mouse-down-and-drag onto
  another photo.
- **Bind once, swap callbacks.** These grids re-render into the same
  container element, so naively attaching listeners on each render stacks
  duplicates (each drag would fire N times). The helper binds once and just
  replaces its callbacks on later calls.

## 19. The storage panel

`/api/status` now includes disk total/used/free (`shutil.disk_usage` on the
mounted data directory, i.e. the Pi's real filesystem) plus the size of the
crops folder and database. Summing ~30,000 files on every poll would be
wasteful — every open tab polls every few seconds — so the result is cached
for 60 seconds and computed off the event loop with `asyncio.to_thread`.

## 20. Where disk space actually goes on the Pi

GuardSense's own data turned out to be tiny; the disk was mostly Docker.

| What | Size | Why |
|---|---|---|
| Docker build cache | ~13 GB | Every rebuild leaves layers behind, including the failed CUDA/torchvision attempts |
| Docker images | ~8.5 GB | The 7 running containers |
| Old `~/guardsense-pi-test` | ~6 GB | Pre-Docker test folder with its own CUDA-build venv |
| OS, journal, apt, pip cache | ~7 GB | Normal |
| Container logs | ~5 MB | Not a problem |

`docker builder prune` (no `-a`) removes only *unused* cache — safe for
running containers, images and data; the only cost is that the next
rebuild of the capture image re-downloads torch (~5 min instead of ~30 s).
That freed 7.7 GB (61% → 50% used).

```bash
df -h /                                   # overall
docker system df                          # images vs build cache vs containers
du -xh --max-depth=1 ~ | sort -rh | head  # biggest folders
sudo journalctl --disk-usage
docker builder prune -f                   # unused build cache only
```

## 21. Running it local-only

The public route (`guard.<domain>` → Nginx Proxy Manager → relay on 8080)
was switched off by disabling that one proxy host in NPM — the clean way,
since NPM's database and generated nginx config stay in sync and it can be
re-enabled with a toggle. Editing nginx's generated config file directly
would work immediately but NPM regenerates it, so the change wouldn't stick.
The relay listens on `0.0.0.0:8080`, so LAN access is unaffected. coturn and
its router port-forwards only matter for remote WebRTC, so they can stay or
be closed; leaving router forwards open while the host is disabled just
exposes ports that lead nowhere useful.

## 22. CI/CD: push to `main` deploys to the Pi

**Goal:** stop hand-copying files with `scp` and rebuilding over SSH. A push
to `main` should update the Pi.

**Options weighed:**
| Option | Verdict |
|---|---|
| **Self-hosted GitHub Actions runner on the Pi** | **Chosen.** The Pi only makes *outbound* connections to GitHub, so nothing is opened on the router; you get GitHub's UI, logs, manual re-run button and per-commit history for free. |
| Webhook listener on the Pi | Needs an inbound port (or a tunnel) and its own signature checking - more attack surface for the same result. |
| Cron/polling script (`git fetch` every minute) | Works and is simple, but no logs UI, no status per commit, and it wakes up constantly for nothing. |
| GitHub-hosted runner that SSHes into the Pi | Needs the Pi reachable from the internet (and now the public domain is off). |

**How it's put together** (`.github/workflows/deploy.yml`, `deploy/`):
1. **`check` job (GitHub-hosted `ubuntu-latest`):** `python -m compileall`
   over every package and `bash -n` on the scripts. A syntax error never
   reaches the Pi. It has to pass before deploy starts (`needs: check`).
2. **`deploy` job (`runs-on: [self-hosted, pi5]`):** checks out the commit
   and runs `deploy/deploy.sh`.
3. **`deploy.sh`** maps the repo onto the two service folders the images are
   built from (`~/npm/guardsense-relay`, `~/npm/guardsense-capture` - they're
   *not* a git checkout, which is why it can't just `git pull`):
   - Everything in `streaming/` goes to the capture folder if it's one of
     `capture_node`, `identity_matcher`, `export_ncnn`, `__init__`; every other
     `.py` goes to the relay. So a *new* UI module deploys without editing
     the script.
   - `camera/ detection/ tracking/ embedding/ DataClass/` go to capture.
   - The per-service Dockerfile, compose file and requirements are copied to
     the names each folder expects.
   - Files are compared by **content hash** (`cmp`, `rsync -c`), not
     timestamps, so a fresh checkout (new mtimes everywhere) doesn't look like
     "everything changed".
   - **Only a service with a real change gets rebuilt.** A UI tweak restarts
     the relay in seconds and never interrupts the capture node.
   - It **never touches** `.env`, `data/`, or `yolov8n.pt` (the weights are
     gitignored, so they exist only on the Pi). `FORCE=1` rebuilds both;
     `DRY_RUN=1` prints what would change and modifies nothing.
4. **Health check:** the relay must answer on `:8080/login`; the capture
   container must still be running 30 s after start (models take ~20 s to
   load). A failure exits non-zero, so the workflow goes red and dumps the
   container's log tail.

**Deliberate design points**
- **No `pull_request` trigger.** A self-hosted runner executes the workflow
  on your hardware, so it must never run code from a fork's PR. Only pushes
  to `main` (and a manual "Run workflow" button) deploy. If the repo is
  public, also set Settings -> Actions -> "Require approval for outside
  collaborators".
- **`concurrency` group, no cancel-in-progress.** Two quick pushes queue
  instead of racing two `docker compose` runs against each other - and a
  half-finished image build is never killed midway.
- **`paths-ignore: **.md`** - editing docs doesn't rebuild containers.
- **Secrets stay on the Pi.** `.env` files aren't in the repo and the script
  can't overwrite them; nothing sensitive is stored in GitHub.
- **`.gitattributes` forces LF** on `*.sh`, `*.yml` and Dockerfiles. A script
  checked out on Windows with CRLF endings fails on Linux with the baffling
  `bash: ...\r: command not found`.
- **Rollback is `git revert` + push** (which redeploys). There's no automatic
  rollback: after `docker compose up --build` the previous image tag is gone,
  and a proper blue/green setup is overkill for one Pi.

**Runner setup** (`deploy/setup-runner.sh`, run once on the Pi): downloads the
right `actions-runner-linux-arm64` build, registers it with a short-lived
token from GitHub (Settings -> Actions -> Runners), labels it `pi5`, and
installs it as a systemd service so it survives reboots. The runner user
must be in the `docker` group.

**Trade-offs to know about:** ARM rebuilds are slow when requirements or a
Dockerfile change (torch reinstalls, ~5 min) but fast for code-only changes
(~30 s) thanks to Docker's layer cache - which is why `docker builder prune`
(§20) has a cost. And the deploy briefly restarts the container it rebuilds,
so a live WebRTC viewer reconnects.

## 23. Persisted alert history

**Problem:** alerts lived only in the relay's memory (a capped list the
capture node re-sent), so every restart or deploy wiped the history - and
there was no way to ask "what happened yesterday?".

**Design:** an `alerts` table in the same SQLite file, and a
`/alerts` page (date picker, camera filter, per-person count chips).
- **Stable IDs.** The capture node mints a UUID (`id`) and epoch (`ts`) when a
  track first appears. `track_id` alone can't be the key - the counter restarts
  from 1 whenever the capture container restarts, so `front_gate:5` on Monday
  and Tuesday are different people. The UUID makes the write an **upsert**:
  when a later embedding cycle matches the track to "Dad", the same row's
  label and `person_id` are updated instead of a duplicate being added.
- **Query by day** uses the epoch, converted from `YYYY-MM-DD` in the server's
  local timezone (which is why the container timezone fix in §14 matters here
  too) - a day is `[00:00, next 00:00)`.
- **"Unknown"** in the summary chips means `person_id IS NULL` - never matched
  to a named person.
- **Retention:** alerts older than `ALERT_RETENTION_DAYS` (30) are deleted by
  the same hourly job, so this table can't become the next thing that grows
  forever.
- The live panel on the main page is unchanged (latest 100); `/api/alerts`
  with no parameters still returns just that. Old capture builds that don't
  send an `id` are tolerated - their alerts are simply not persisted.

## 24. Audit log

**Problem:** crops vanished once and nothing could say who did it or when.

**Design:** every destructive admin action (`assign`, `merge`,
`delete_sighting`, `bulk_delete_sightings`, `delete_person`, `move_crops`,
`delete_crop(s)`) calls one `audit()` helper that (1) prints a line (so
`docker logs` has it), (2) pushes it to the Logs page as an `AUDIT` line, and
(3) inserts a row into `audit_log` - so it survives restarts. Each entry
records *what* (`"Dad" (id 2) and 300 crop(s)`, resolved to names *before* the
delete, since afterwards the name is gone), *when*, and *from where*
(`X-Forwarded-For` if present, else the socket address - the reverse proxy
would otherwise make every request look like `127.0.0.1`). It's a record of
actions, not authentication: there is one shared admin password, so it can say
which device, not which person.

## 25. Memory that grew forever in the capture node

ByteTrack's ID counter only ever climbs and never reuses an ID, and the node
kept a dict entry per track in several places, none of which ever shrank:
`seen_track_ids` (a set), `alert_order` (a list), `track_identity` and
`crop_sent`. Days is fine; months isn't. Fixes:
- `seen_track_ids` became `{track_id: last_seen_at}` so idle entries can be
  identified; every ~600 loops `_prune_track_state()` drops tracks unseen for
  an hour (`TRACK_STATE_TTL`) and then removes `track_identity` and
  `crop_sent` entries for any track no longer live.
- The alert list became an `OrderedDict` keyed by `(camera, track)` capped at
  500 (`ALERT_MEMORY`) - it must stay keyed (not a plain list) so a later name
  match can still update an alert in place. Full history is in SQLite now, so
  this is only the recent window that can still be edited.
- Safe because a track ByteTrack has dropped is gone for good: an hour of
  silence means it will never come back with the same ID.

## 26. Emptied people, and splitting one person into two

- **Empty-person embedding gap fixed:** `_recompute_person_embedding` used to
  do nothing when zero crops remained, leaving a stale mean vector so a person
  with no crops could still match new detections. It now sets the embedding
  to NULL (the name stays; matching stops).
- **"+ New person…" in the gallery's Move dropdown** covers the case that
  started this: two different people ended up under one name. Select the
  wrong crops, choose *+ New person…*, name them - one request
  (`new_person_name`) creates the person and moves the crops, and both the old
  and new people's embeddings are recomputed.

## 27. Cameras that aren't there yet

When the DVR was powered off, every camera failed at startup and the capture
node exited; Docker restarted it every few seconds, forever. Worse, if only
*one* camera was down at startup it was dropped for the life of the process.
`CameraManager` now never raises on an unreachable camera: the reader thread
starts anyway and its reconnect loop keeps retrying. It also **clears the
last good frame when the link drops**, so a dead camera can't keep being
served as if it were live. (`.140` vs `.141` - the DVR was simply off; checked
by opening a TCP socket to port 554 from the Pi, not by ping, since a camera
port can answer while ICMP is filtered.)

## 28. CI/CD runbook: how we deployed it, the commands, and how to debug it

§22 explains *why* the pipeline is shaped the way it is. This section is the
*how*: what was actually run, and what to do when it breaks.

### 28.1 The pieces

| Piece | Where | Purpose |
|---|---|---|
| `.github/workflows/deploy.yml` | repo | Defines the two jobs: `check` (GitHub-hosted) then `deploy` (Pi) |
| `deploy/deploy.sh` | repo | The actual deploy: sync changed files, rebuild what changed, health-check |
| `deploy/setup-runner.sh` | repo | One-time: installs + registers the runner on the Pi |
| `~/actions-runner/` | Pi | The runner program (listener + worker), its config and logs |
| `~/actions-runner/_work/GuardSense/GuardSense/` | Pi | The runner's fresh checkout of the commit being deployed |
| `~/npm/guardsense-relay/`, `~/npm/guardsense-capture/` | Pi | The folders the images are actually built from (not a git checkout) |
| systemd unit `actions.runner.<owner>-<repo>.pi5.service` | Pi | Keeps the runner alive across reboots |

The flow: `git push` -> GitHub sees a push to `main` (docs-only changes are
ignored) -> `check` job compiles the code on a GitHub-hosted machine -> `deploy`
job is handed to **our** runner (label `pi5`) -> it checks out the commit ->
`bash deploy/deploy.sh` -> containers rebuilt -> health check -> green or red.

### 28.2 Setting it up (what we ran, in order)

1. **Get a registration token.** GitHub repo -> Settings -> Actions -> Runners
   -> *New self-hosted runner* -> Linux / ARM64. Copy the token from the
   `./config.sh --url ... --token XXXX` line. It is single-use and expires in
   about an hour; it only lets a runner register, it isn't a login credential.
2. **Copy the script to the Pi and run it:**
   ```bash
   scp deploy/setup-runner.sh nunna@<pi-ip>:/tmp/
   ssh nunna@<pi-ip>
   bash /tmp/setup-runner.sh navyukth/GuardSense <registration-token>
   ```
   (Over a non-interactive SSH session there's no terminal for the `sudo`
   password, so we ran it as
   `printf '<pw>\n<pw>\n<pw>\n' | SUDO='sudo -S' bash setup-runner.sh <owner>/<repo> <token>`.)
   The script downloads the latest `actions-runner-linux-arm64`, runs
   `./config.sh --unattended --replace --name pi5 --labels pi5`, then
   `sudo ./svc.sh install <user>` and `sudo ./svc.sh start`.
3. **Check it registered:** GitHub -> Settings -> Actions -> Runners should show
   `pi5` as **Idle** (green). On the Pi:
   ```bash
   systemctl status actions.runner.navyukth-GuardSense.pi5.service
   ```
4. **Prerequisites that must already be true on the Pi:** the runner user is in
   the `docker` group (`id` lists `docker`), `rsync` and `git` are installed, the
   two service folders exist and each already contains its `.env`, and
   `~/npm/guardsense-capture/yolov8n.pt` exists (the weights are gitignored).
5. **Dry-run first** (changes nothing) against a copy of the repo:
   `DRY_RUN=1 bash deploy/deploy.sh`. It printed the relay as unchanged - proof
   the file mapping matched what was live - and one real difference.
6. **Push.** A job queued *before* the runner existed just waits (GitHub keeps
   queued jobs for up to 24 h), so the moment the runner registered it picked up
   the earlier push and deployed it. Then a scripts-only push confirmed the
   no-op path (~7 s, nothing rebuilt).

### 28.3 Day-to-day commands

```bash
# --- from your laptop ---
git push origin main                    # deploys (unless the change is docs-only)
# GitHub -> Actions tab -> "Deploy to Pi5" -> Run workflow (tick "force" to rebuild both)

# --- on the Pi ---
bash deploy/deploy.sh                   # deploy what changed, by hand
DRY_RUN=1 bash deploy/deploy.sh         # preview only - modifies nothing
FORCE=1   bash deploy/deploy.sh         # rebuild both containers regardless

docker ps                               # are both containers up?
docker logs --tail 50 guardsense-capture
docker logs --tail 50 guardsense-relay
docker inspect -f '{{.State.Running}} {{.RestartCount}}' guardsense-capture
```

Roll back a bad deploy: `git revert <bad-commit>` then `git push` - that
redeploys the previous code. There is deliberately no automatic rollback.

### 28.4 Where to look when something's wrong

| Question | Look here |
|---|---|
| Did my push deploy? What happened? | GitHub -> **Actions** tab -> the run -> click the `Deploy` job; the full `[deploy] ...` output and any Docker build log is there |
| Is the runner alive? | `systemctl status actions.runner.navyukth-GuardSense.pi5.service`; live log: `journalctl -u actions.runner.navyukth-GuardSense.pi5.service -f` |
| Runner-side details for a job | `~/actions-runner/_diag/Runner_*.log` (the listener) and `Worker_*.log` (one per job - search for `Job result`) |
| What did the runner check out? | `git -C ~/actions-runner/_work/GuardSense/GuardSense log --oneline -1` |
| What's actually running on the Pi? | `docker ps`, `docker logs <container>` |

Note the per-step output lives in the GitHub Actions UI; the runner's
`_diag/pages/` copies are deleted when the job ends, so don't rely on them
after the fact.

### 28.5 Failure modes and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `deploy` job sits **Queued** forever | Runner offline, or no runner has the `pi5` label | `systemctl status` the service, `sudo systemctl restart` it; check the Runners page shows it *Idle* and labelled `pi5` |
| Runner shows **Offline** in GitHub | Service stopped, Pi rebooted without the unit enabled, or no internet | `sudo systemctl enable --now actions.runner...service`; check the Pi's connectivity |
| `check` job fails | A syntax error (caught before the Pi is touched - working as intended) | Fix the error the log names and push again |
| Deploy red: `relay: NOT answering on :8080` | Relay crashed on start (bad code/config) | The job log already dumps `docker logs --tail 30`; fix and push, or `git revert` |
| Deploy red: `capture: NOT running` | Capture exited (models, cameras, config) | Same; also `docker logs guardsense-capture` |
| `permission denied ... docker.sock` | Runner user isn't in the `docker` group | `sudo usermod -aG docker <user>`, then restart the runner service (group changes need a new session) |
| `ERROR: <folder> doesn't exist` | A service folder was never created | Create it and put its `.env` there first |
| Build fails: `COPY yolov8n.pt` not found | Weights are gitignored, so they must already be on the Pi | Put `yolov8n.pt` in `~/npm/guardsense-capture/` |
| Deploy rebuilds **both** every push | File comparison is timestamp-based somewhere | It should compare content (`cmp`, `rsync -c`); check you haven't edited `deploy.sh` to use `-t`/mtimes |
| `bash: ...\r: command not found` | A script got CRLF line endings on Windows | `.gitattributes` forces LF; re-checkout, or `dos2unix` |
| Build very slow (~5 min) | `requirements.txt` or a Dockerfile changed, invalidating the cache | Expected; or the build cache was pruned (§20) |
| `no space left on device` during build | Docker build cache filled the SD card | `docker builder prune -f` (§20) |
| Registration: `Http response code: NotFound` | Token expired or already used | Generate a new token on the Runners page |

### 28.6 Re-registering or removing the runner

```bash
cd ~/actions-runner
sudo ./svc.sh stop && sudo ./svc.sh uninstall
./config.sh remove --token <removal-token>     # token from GitHub's Runners page -> the runner -> Remove
cd ~ && rm -rf actions-runner
# then run deploy/setup-runner.sh again with a fresh registration token
```
(`setup-runner.sh` refuses to run if `~/actions-runner/.runner` already exists,
so a half-configured runner has to be removed first.)

### 28.7 What went wrong building it (and what to remember)

- **`curl | grep -m1` under `pipefail`** aborted the setup script on its very
  first run (`grep` exits early, `curl` errors writing to the closed pipe).
  Capture the output in a variable first.
- **No terminal for `sudo` over SSH** - hence the overridable `SUDO` variable.
- **PowerShell quoting** broke most of my monitoring one-liners (`$(seq ...)`
  expanded locally, `/dev/tcp` treated as a path). Every check became a small
  script copied to the Pi and run there.
- **A monitoring script looked for log files the runner had already deleted**
  and spewed errors - the deploy itself was fine. Trust the GitHub Actions UI
  and `docker ps`, not scraped runner internals.
- **I corrupted a doc while "fixing" it.** Swapping a password for a
  placeholder using PowerShell's `Get-Content`/`Set-Content` re-encoded the
  UTF-8 file as if it were ANSI, turning every em-dash into `â€”` (62 places).
  It was caught before pushing and reversed exactly by re-encoding the bytes.
  Lesson: never round-trip a UTF-8 file through PowerShell 5.1's default
  encodings - use the editor tool, or pass `-Encoding UTF8` and read with
  `[IO.File]::ReadAllText(path, UTF8)` - and always `git diff` after a scripted
  edit. (Also: `Set-Content -Encoding UTF8` in 5.1 adds a BOM.)
- **Security decision, not a bug:** no `pull_request` trigger, because a
  self-hosted runner executes the workflow on your own hardware.

## 29. Commands used throughout (reference)

Local dev / laptop:
```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
python -m streaming.relay_server      # one process
python -m streaming.capture_node      # another
python -m py_compile <files>          # quick syntax check before deploying
```

Pi5 deployment (both services):
```bash
cd ~/npm/guardsense-relay && docker compose up -d --build
cd ~/npm/guardsense-capture && docker compose up -d --build
docker ps
docker logs -f guardsense-capture
docker logs --tail 50 guardsense-relay
```

NCNN export (baked into the capture image's build step, or run manually):
```bash
python -m streaming.export_ncnn 320
```

Benchmarking (measuring real throughput, not guessing):
```bash
curl -s -c cj.txt -d "password=<ADMIN_PASSWORD>" http://localhost:8080/login -o /dev/null
curl -s -b cj.txt http://localhost:8080/api/status   # capture_loop_count, sampled twice N seconds apart
```

systemd (an intermediate step before full Docker deployment, kept as a
reference for running the capture node as a plain OS-level service
instead of a container, e.g. for debugging without a rebuild):
```ini
[Unit]
After=network-online.target docker.service
[Service]
ExecStart=/path/to/.venv/bin/python -u -m streaming.capture_node
Restart=always
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now guardsense-capture.service
sudo systemctl status guardsense-capture.service
```

Git (see `challenges faced and bugs we faced.md` for the divergent-history
incident this relates to):
```bash
git fetch origin
git log --oneline --graph --all
git push origin origin/main:refs/heads/archive/<name>   # back up before rewriting history
git push --force-with-lease origin main                  # safer than --force - fails if remote moved since your last fetch
```
