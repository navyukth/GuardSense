# Challenges, Bugs, and Errors Faced

Every notable problem hit while building/deploying this project, in
roughly the order encountered, with root cause and fix. Minor typos and
one-line fixes are skipped — this is the list of things that actually
cost time or revealed something worth remembering.

---

## 1. Environment access problems (before any real work could start)

**Problem:** No way to reach the Pi5 non-interactively. SSH with a
password hangs forever, because the automated shell's stdin is a null
device — there's no terminal to type a password into, and no way for a
password prompt to be answered.

**Fix:** Generated a dedicated SSH keypair, had the user append the
public key to the Pi5's `~/.ssh/authorized_keys` manually (one command,
run by the user directly on an already-open SSH session). All later
Pi5 access used `ssh -i <keyfile>` — no password needed.

**Related problem:** the same laptop initially had **no Python and no
Git installed at all** — not even a base interpreter. Had to be
diagnosed (commands silently "not recognized") before anything else
could run. Fixed with `winget install Python.Python.3.12` and
`winget install Git.Git`.

**Gotcha discovered along the way:** after installing, `python`/`git`
still weren't found in the *same* PowerShell session — each tool
invocation in this environment is effectively a fresh process, and
`$env:PATH` changes don't persist between separate tool calls. Every
subsequent command that needed the newly-installed tools had to
explicitly re-read `PATH` from the registry
(`[System.Environment]::GetEnvironmentVariable("PATH","Machine")`) at
the start of that command.

## 2. PowerShell ↔ SSH ↔ bash quoting hell

**Problem:** Commands like
`ssh ... "python -c 'import x; print(\"...\")'"`
kept failing with syntax errors — PowerShell, SSH, and the remote bash
shell each have their own quoting rules, and nesting three of them in
one line reliably breaks something.

**Specific instances:**
- `date +%s` inside a double-quoted PowerShell string got interpreted
  as PowerShell's own `$(...)` subexpression syntax (tried to run
  `Get-Date` instead of bash `date`).
- A multi-line `git commit -m @'...'@` heredoc containing
  `Person #<track_id>` broke because of the embedded `<`/`>` characters
  — PowerShell treats those as redirection operators even inside a
  here-string when the whole thing gets handed to a native executable.
- Inline Python one-liners via `ssh ... "python -c '...'"` kept
  mangling quotes across the SSH/bash/python boundary.

**Fix, consistently:** stop trying to inline complex commands. Write
the Python snippet or the commit message to a **local file**, `scp` it
over (or use `git commit -F <file>` instead of `-m`), and run the file
directly. Zero quoting ambiguity once the content isn't living inside
nested string literals.

## 3. Backgrounding a remote process over SSH doesn't reliably survive

**Problem:** `ssh host "nohup long_running_thing &"` looked like it
started successfully (command returned), but the process was later
found not running at all — it had died silently, no error.

**Root cause:** in a non-interactive `bash -c "..."` context (which is
exactly how SSH runs a remote command), job control isn't enabled by
default, so `disown` doesn't reliably detach the background job from
the parent shell. When the SSH session's shell exits, the "backgrounded"
child can still get cleaned up despite `nohup`.

**Fix:** for anything that needs to actually finish (a Docker build in
particular), run it **synchronously in the foreground** with a long
enough timeout, rather than trying to background-and-poll it over SSH.
Slower to get a result back, but it actually completes.

## 4. The 1-minute live-feed lag (RTSP buffering)

**Symptom:** the live feed was real video, just ~60 seconds stale.

**Root cause:** `cv2.VideoCapture`'s FFMPEG backend doesn't reliably
honor `cv2.CAP_PROP_BUFFERSIZE`. When the consumer (`cap.read()`,
called once per pipeline loop) is slower than the camera's frame rate
— guaranteed once you're doing CPU-bound YOLO detection across 4
cameras every loop — the RTSP demuxer's internal buffer backs up and
every read returns progressively older unread frames.

**Fix:** a dedicated reader thread per camera continuously drains the
stream and keeps only the single latest frame; the main pipeline reads
that instead of calling `cap.read()` directly. Full explanation in
`Explanation.md` section 2.

## 5. Camera reader had no reconnect logic

**Symptom:** after the threaded-reader fix above, if an RTSP connection
ever dropped, that camera's feed would freeze **permanently** — no
crash, no error, just a silently dead thread spinning on `read()` calls
that would never succeed again.

**Fix:** track consecutive read failures; after 10 in a row, release
and reopen the connection with a short delay. Added logging so this is
visible instead of silent.

## 6. Relay served a frozen frame forever after the capture node died

**Symptom:** stopped the capture process to test something, and the
browser kept showing what looked like a perfectly normal (but
completely stale) live feed — no visual indication anything was wrong.

**Root cause:** the relay's `FrameStore` just holds "the last frame
ever received" per camera, with no timestamp check — `RelayVideoTrack`
happily kept re-serving the same image indefinitely.

**Fix:** track a per-camera `frame_updated_at`; if a frame is older
than `FRAME_STALE_SECONDS` (5s), treat it as absent and render a "No
live feed (capture node disconnected)" placeholder instead.

## 7. "Cameras randomly going black for 1-2 seconds" — a real investigation, not a bug in our code

**Symptom:** all 4 camera feeds would occasionally go black together
for 1-2 seconds, seemingly correlated with detections happening.

**Investigation:** checked the reconnect-logic logs first — zero
reconnect events logged at the times this happened, which ruled out
our own reader threads as the cause. To be certain, **stopped every
one of our own processes entirely** (capture node, everything) and had
the user watch the DVR's own output directly. The blackout still
happened with zero code of ours running.

**Conclusion:** this is DVR/camera hardware behavior (most likely
IR-cut/night-vision switching), entirely outside the pipeline's
control. Worth recording specifically *because* the instinct was to
assume it was a software bug — the isolation test (turn everything off,
see if it still happens) is what actually settled it.

## 8. `torchreid` package layout differs between installs

**Symptom:** `from torchreid.utils import FeatureExtractor` worked on
one machine, failed with `ModuleNotFoundError: No module named
'torchreid.utils'` on another.

**Root cause:** the newer PyPI `torchreid` release (0.2.5) nests
everything under `torchreid.reid.*`; an older install (1.4.0, from a
different source) keeps `FeatureExtractor` at the top level
`torchreid.utils`. `from x.y import z` in Python needs `x.y` to
actually resolve as a submodule — a name that's merely bound as an
attribute during package `__init__` (which is what happens in the
newer layout when it re-exports `reid.utils` as `torchreid.utils`)
doesn't satisfy that.

**Fix:** wrap the import in a try/except that tries the new layout
first, falls back to the old one:
```python
try:
    from torchreid.reid.utils import FeatureExtractor
except ImportError:
    from torchreid.utils import FeatureExtractor
```

## 9. Missing transitive dependencies, one at a time

Installing `torchreid` via pip did **not** pull in everything it
actually needs at import time. Hit a chain of `ModuleNotFoundError`s
one after another, each requiring a manual `pip install`: `scipy`,
`gdown`, `yacs`, `h5py`, `Cython`, `tb-nightly`, `future`. All added
explicitly to `requirements.txt` afterward so a fresh install doesn't
have to rediscover this same chain.

Separately, Ultralytics' ByteTrack integration does its own runtime
**auto-install** of `lap` the first time it's needed (prints
"AutoUpdate success", then a "restart runtime" warning) — harmless but
surprising the first time you see it. Added `lap` to
`requirements.txt` explicitly to avoid the runtime surprise/delay.

## 10. Wrong assumption about laptop hardware

The recovered `.env` from the Pi5 had a comment claiming
`INFERENCE_DEVICE=cuda` was correct "on this laptop (RTX 3050)". That
config was from a **different, older laptop** — this Windows machine
has no discrete GPU at all (Intel UHD 620 integrated graphics only,
confirmed via `Get-CimInstance Win32_VideoController` and the absence
of `nvidia-smi`). Had to switch to `INFERENCE_DEVICE=cpu` and adjust
expectations (and later, `YOLO_IMGSZ`) accordingly. Lesson: recovered
config files describe the machine they were written on, not
necessarily the machine you're running them on now.

## 11. stdout buffering hid output when redirected to a file

**Symptom:** ran `capture_node.py` redirected to a log file, waited,
file was empty even though the process was clearly running (using
CPU). Looked hung.

**Root cause:** Python buffers stdout differently depending on whether
it's attached to a terminal or a file/pipe — redirected to a file, it's
block-buffered, so `print()` output doesn't actually hit disk until the
buffer fills or the process exits.

**Fix:** run with `python -u` (or set `PYTHONUNBUFFERED=1`), which
forces unbuffered output. Note this resurfaced in a different shape
later — see #13.

## 12. Docker build pulled ~1GB+ of unused CUDA/cuDNN on the Pi5

**Symptom:** a Docker build on the ARM Pi5 (no NVIDIA GPU, ever) was
downloading a 454MB `torch` wheel followed by a 651MB `nvidia_cudnn`
wheel, with more CUDA packages queued after that.

**Root cause:** plain `pip install torch` (no index specified) grabs
PyPI's default wheel, which includes CUDA dependencies unconditionally
on Linux — regardless of whether the target hardware has an NVIDIA GPU.

**Fix:** verified first (via a `pip install --dry-run` dry run in a
throwaway container) that PyTorch publishes a genuine CPU-only ARM64
wheel at `https://download.pytorch.org/whl/cpu` (159MB, no CUDA), then
changed the Dockerfile to install from that index explicitly, before
the rest of `requirements.txt`.

## 13. torch/torchvision version mismatch crash-looped the container

**Symptom:** after fixing #12, the container built successfully but
crash-looped immediately with:
```
RuntimeError: operator torchvision::nms does not exist
```

**Root cause:** the Dockerfile installed `torch` alone from the CPU
index, but left `torchvision` to be pulled in later as an unpinned
transitive dependency of `torchreid` (via the regular PyPI index) —
resulting in a `torchvision` build that doesn't match the specific
`torch` build already installed, breaking native operator registration.

**Fix:** install `torch` **and** `torchvision` together, in the same
`pip install` command, from the same CPU index — guarantees a
compatible pair.

## 14. A self-deadlocking custom logging handler

**Symptom:** after adding the Logs feature (see `Explanation.md`
section 13), the Docker container would start, print a couple of
startup log lines, and then just... stop. No crash, no error, no
further output. `docker stats` showed ~3% CPU — essentially idle, not
computing anything.

**Root cause:** `logging.Handler`'s base class already uses an
attribute named `self.lock` internally (`handle()` calls
`self.acquire()`, which acquires `self.lock`, *then* calls `emit()`).
The custom ring-buffer handler's `__init__` did
`self.lock = threading.Lock()`, silently **overwriting** that internal
attribute with a fresh, non-reentrant lock. The very first log message
routed through the handler: `handle()` acquires the lock, calls
`emit()`, which tries `with self.lock:` — attempting to acquire the
*same* non-reentrant lock again, on the same thread. Deadlock, forever,
on the first log call.

**Why it was confusing:** the process wasn't crashed (no traceback),
wasn't obviously "stuck" from a status/health-check point of view (the
container reported "running"), and the low CPU usage looked more like
"still loading something" than "permanently deadlocked" until a `docker
logs -f` watched for 30+ seconds with zero new output confirmed it
truly wasn't progressing.

**Fix:** renamed the custom lock attribute to `self.buffer_lock` —
trivial fix once identified, but the identification took a live
container in a crash-loop-that-wasn't-quite-a-crash-loop to pin down.

**Process lesson:** this bug was caught and fixed on the **laptop**
first (quick local repro) before redeploying to the Pi5, rather than
iterating on slow Docker rebuilds — much faster feedback loop.

## 15. A truncated `.env` file glued two variables together

**Symptom:** `YOLO_MODEL` came back empty/wrong after an `.env` update.

**Root cause:** an earlier `.env` write didn't end with a trailing
newline; a later `>>` append landed on the same line as the previous
variable, producing `CROPS_DIR=cropsYOLO_MODEL=yolov8n_ncnn_model` — one
corrupted line instead of two valid ones.

**Fix:** stopped appending to `.env` files piecemeal; rewrote the whole
file at once from a single known-good template each time a value
needed to change.

## 16. `.gitignore`'s `.env.*` pattern also matched `.env.example`

**Symptom:** `.env.example` (meant to be committed, template only, no
real secrets) wasn't showing up in `git status` as a new file at all.

**Root cause:** the existing `.gitignore` had `.env` and `.env.*` —
the wildcard pattern matches `.env.example` too, since it starts with
`.env.`.

**Fix:** added a negation rule immediately after: `!.env.example`.

## 17. A credentials file almost got committed

**Caught before it became a real leak, but worth recording:**
`serverpi.md` (real Pi SSH password, TURN credential, API keys) was
sitting in the repo, untracked, with **no** `.gitignore` rule covering
it — `git status` showed it as a new, addable file. Added an explicit
`.gitignore` entry (`serverpi.md`, with a comment explaining why) before
ever running `git add -A`. General lesson: always read through
`git status`/`git diff` output for anything untracked-but-sensitive
before staging broadly, not just rely on existing `.gitignore` rules
being complete.

## 18. Discovering a month of unfetched, diverged GitHub history (see also #19-#25 below, added later)

**Symptom:** a routine `git push` was rejected with "Updates were
rejected because the remote contains work that you do not have
locally" — surprising, since the session had started with `git status`
reporting "up to date with origin/main".

**Root cause:** this local repo folder was literally copied from an
HDD backup made back in August — a point-in-time snapshot, not a live
clone that had been `git fetch`-ed since. Its cached knowledge of
`origin/main` was frozen at that snapshot's commit. `git status`
without a fetch only compares against that *cached* remote-tracking
ref, not GitHub's actual current state — so it correctly reported "up
to date" relative to stale information, which looked identical to
actually being up to date until a real `git fetch` ran.

**What was actually on GitHub:** 9 commits from August 2026 containing
a **different, more complete implementation** of the same idea —
`streaming/server.py` (an all-in-one server), `streaming/
guardsense_pipeline.py` (a `GuardSensePipeline` orchestrator class),
`database/db.py`, `reid/reid_matcher.py` — none of which existed
anywhere in the freshly-rebuilt version (which had been reconstructed
from scratch off files found on the Pi5's filesystem, independently,
without knowing this GitHub history existed).

**Resolution:** before doing anything destructive, pushed the
diverged `origin/main` to a separate backup branch
(`archive/august-2026-old-implementation`) so that history stays
reachable and recoverable, *then* force-pushed (`--force-with-lease`,
which fails safely if the remote moved again in between, rather than
blindly overwriting) the current, tested, working implementation as
the new `main`.

**Lesson:** `git status` at the start of a session is not proof of
being current with a remote — a real `git fetch` is, especially when
working from a repo copy whose provenance (was this cloned recently,
or copied from somewhere?) isn't fully known.

---

# Later incidents (after the system had been running unattended for days)

## 19. "Alerts stopped 5.5 hours ago" — they hadn't; the container was in UTC

**Symptom:** opened the app at 12:25 PM and the newest alert was from
~06:55. It looked like detection had died mid-morning.

**Investigation (what ruled things out):**
- The capture loop counter was climbing (~2,000,000 loops, ~5 loops/s), so
  the pipeline was alive.
- The relay held exactly 100 alerts (the cap), the newest stamped 06:58.
- A per-30-minute crop timeline showed continuous, normal-looking activity.
- The container's own clock read `07:02` while the host's `date` said
  `12:27 IST` — a gap of exactly 5h30m, i.e. IST's UTC offset.

**Root cause:** Docker containers default to UTC. The unix epoch in the
database was right; only the human-readable rendering (`time.strftime`) used
the container's UTC timezone.

**Fix:** `TZ=Asia/Kolkata` in both compose files **and** the `tzdata`
package in both Dockerfiles (Debian slim doesn't include it, and `TZ` does
nothing without it). Verified with `docker exec ... date` showing IST.

**Lesson:** when "the data stopped", first check whether the *clock* is what
moved. Compare timestamps between host and container before hunting a hang.

## 20. "34,732 people" was the tracker counter, mislabeled

The user saw huge numbers and reported tens of thousands of persons. The
database had **3** named people. The number was the raw ByteTrack
`track_id` (which only ever increases — ~38,000 after 82 h), exposed in
the alert JSON under the misleading key `person_id`.

**Fix:** alerts now carry `track_id` (raw counter) and `person_id` (real
identity or `null`) as separate fields; the UI fallback label uses
`track_id`. A restart also resets the counter, so it stops looking alarming.

## 21. A wrong hypothesis, caught by data

While chasing #19, the first theory was a *single stuck track* — one
false-positive object (a parked bike?) tracked continuously for hours, never
producing a "new" track and so never a new alert. The evidence disagreed:
the "stuck" track had exactly one crop, and dozens of distinct short-lived
tracks appeared in the last hour. Pulling a sample crop (a real person in
dark clothing) and bucketing crops by time is what redirected the
investigation to the clock. Worth remembering: form a hypothesis, then look
for the observation that would *disprove* it.

## 22. Bikes and shadows detected as people

**Cause:** `YOLO_CONFIDENCE` was a hard-coded 0.3 (very permissive) and
`imgsz` had been dropped to 320 for speed, which makes ambiguous shapes
harder to classify. Since only class 0 ("person") is kept, a bike can only
get through by being *misclassified* as a person — there's no exclusion
list to add it to.

**Fix:** confidence made configurable and raised to 0.5; bulk-delete added
to the People page for the leftovers. Trade-off: a higher threshold misses
some real, low-confidence detections.

## 23. Storage growth: 30,836 crops and a 125 MB database in days

Every embedding cycle (~1/s per tracked person) shipped a crop, so a person
standing in view produced dozens of near-identical images. One person had
~12,700 crops. Separately, the capture container was *also* writing its own
duplicate copy of every crop (184 MB) to the SD card.

**Fix:** layered — filter and throttle at the source (min size, 10 s
spacing, per-track cap), a rolling 48-hour purge of unnamed crops, a
quality-and-diversity prune of named people down to 300 crops each, `VACUUM`
after big deletes, and the local duplicate copy switched off. Result: 30,836
→ 903 crops, database 125 MB → 3.7 MB. Details in Explanation.md §17.

## 24. "database is locked" while running maintenance

**Symptom:** running a manual cleanup script gave
`sqlite3.OperationalError: database is locked`.

**Cause:** the relay's own hourly retention pass had already started (it
fires 30 s after startup), was mid-`VACUUM`/prune, and a second writer
arrived. SQLite allows one writer, and `VACUUM` takes an exclusive lock.

**Fix:** a 60-second busy timeout on every connection so writers wait
instead of failing, plus a `try/except` around the ingest handler's crop
write — an uncaught error there would have torn down the same websocket
that carries the live video. Also learned to wait for the built-in job to
finish before running a manual one.

## 25. A wait-loop that silently didn't wait

A background "poll until the retention pass finishes" command used
`$(seq 1 110)` inside a double-quoted PowerShell string. PowerShell
expanded `$(...)` itself (and there is no `seq` on Windows), so the loop
never ran and the command reported "still-running" — a result that meant
nothing. Same family as the quoting problems in #2. **Fix:** verify the
actual state directly (read the log) instead of trusting a poll whose
mechanism you haven't confirmed, and keep remote loops out of local shell
expansion.

## 26. "All my crops vanished" — it was the user

The status panel showed `crops_count: 0` and the database had 0 crop rows,
down from 903 a few minutes earlier — alarming, since the last thing run was
a cleanup job. Checked the database, the image folder, the relay log and the
capture log before concluding anything; no code path had deleted them. It
turned out the user had deleted them from the People page. **Lesson:** when
data disappears, gather evidence (rows, files, logs) *before* assuming a bug
in your own recent change — and it's worth having the deletion actions leave
a log line so this is answerable from the logs next time.

## 27. Disk usage mystery: 34 GB used, 4 MB of it ours

**Symptom:** the new storage panel showed 61% of the Pi's SD card in use,
while GuardSense's own data was only ~190 MB.

**Investigation:** `df`, `docker system df` and `du --max-depth=1` showed
~19 GB under `/var/lib/containerd` — Docker: 8.5 GB of images and **13.5 GB
of build cache** left by every rebuild, including the failed CUDA/torchvision
attempts — plus a 6 GB abandoned pre-Docker test folder with its own CUDA-build
venv. Container logs (checked as a suspect) were only ~5 MB.

**Fix:** `docker builder prune -f` (unused cache only) freed 7.7 GB — 61% →
50%. Running containers, images and data are unaffected; the only cost is a
slower next rebuild.

## 28. One Dockerfile line, a 5-minute rebuild

Adding `tzdata` to the apt step (for #19) put a changed layer *above* the
torch and requirements installs, so Docker's layer cache was invalidated from
there down and torch, torchvision and every dependency reinstalled. Not a
bug, but a reminder: order Dockerfile steps from least- to most-frequently
changing, and expect a slow rebuild whenever an early layer changes.

## 29. A person with no crops still matched (fixed)

If every crop of a named person was deleted or moved away, their stored mean
embedding was **not** cleared — the recompute only wrote when at least one
crop remained — so they kept matching new detections with nothing to back it.
Cleared by hand once with `UPDATE persons SET embedding = NULL`; then fixed
properly: the recompute now sets the embedding to NULL when no crops remain
(the name is kept). Covered by a test that deletes a person's crops and moves
*all* of another person's crops away, asserting the source embedding is gone
and the destination's is set.

## 30. Alerts vanished on every restart

Alerts lived only in the relay's memory, so each deploy erased the history and
"what happened yesterday?" was unanswerable. Now stored in SQLite with a
capture-minted UUID so a later name match *updates* the row instead of
duplicating it. A subtlety worth remembering: `track_id` can't be the key,
because the tracker's counter restarts from 1 whenever the capture container
restarts — `front_gate:5` on two different days are different people.

## 31. Four per-track dicts that grew forever (slow memory leak)

The capture node kept an entry per track in `seen_track_ids`, `alert_order`,
`track_identity` and `crop_sent`, and never removed any. Harmless for days;
unbounded over months, since the ID counter only climbs. Fixed with a
last-seen timestamp per track, an hourly prune of anything idle for an hour,
and a capped, keyed alert window. No symptom to observe — found by reading the
code with "what happens after six months?" in mind.

## 32. "Where did my crops go?" — no way to tell

After the crops disappeared (§26) nothing recorded what had happened. Added an
audit log for every destructive admin action: shown on the Logs page and
stored in SQLite. One detail: names are resolved *before* the delete runs,
since afterwards the row (and the name) no longer exists to describe.

## 33. The DVR was off, and the capture node crash-looped

**Symptom:** feed showed "No live feed"; capture container restarting every
few seconds with `No route to host` for `192.168.0.141`.

**Investigation:** ping from the Pi failed (100% loss) and its ARP entry for
the DVR was `INCOMPLETE` - nothing answering at that address; the laptop
couldn't reach it either, so not a Pi-side problem. (`.140` was also tried -
nothing there.) A minute later a TCP connect to port 554 succeeded: the DVR
had been powered off and was coming back.

**Real bug exposed:** at startup, a camera that couldn't be opened was
*dropped for the life of the process*, and if none opened the process exited
and relied on Docker restarting it. A single camera down at boot was never
retried at all. **Fix:** the reader thread now always starts and its
reconnect loop keeps retrying; it also clears the last good frame when the
link drops so a dead camera isn't shown as live.

**Lesson:** test reachability with a TCP connect to the actual service port,
not just ping (a device can answer one and not the other), and treat "the
thing I depend on isn't up yet" as a normal state, not a crash.

## 34. Deploy script pitfalls (found before they cost anything)

- **CRLF line endings.** A shell script edited on Windows and checked out
  there gains `\r`, and Linux then fails with `bash: ...\r: command not
  found`. Fixed with a `.gitattributes` forcing LF for `*.sh`, `*.yml` and
  Dockerfiles.
- **Timestamps lie after a fresh checkout.** Every file has a new mtime, so an
  mtime-based sync thinks everything changed and rebuilds both containers on
  every push. The script compares **content** (`cmp`, `rsync -c`).
- **The weights aren't in git.** `yolov8n.pt` is gitignored, so a deploy that
  synced the folder with `--delete` would have removed the model from the Pi.
  The script only touches named files and directories.
- **A dry run before trusting it.** `DRY_RUN=1` against a copy of the repo on
  the Pi reported the relay as unchanged (proving the file mapping matched
  what was live) and flagged one genuine difference (an older
  `requirements.txt` on the Pi).
- **Self-hosted runner + public repo = remote code execution.** A runner
  executes the workflow on your hardware, so a `pull_request` trigger would
  let anyone's fork run code on the Pi. The workflow triggers only on pushes to
  `main`.

## 35. The shell-quoting problem, again

Two more test commands failed purely because of PowerShell/ssh/bash quoting
(a `/dev/tcp` redirect and a JSON body with escaped quotes) — and one produced
a misleading `500`. Same conclusion as §2 and §25, now applied by reflex: put
anything non-trivial in a small script file, copy it over, run it. Also learned
to read a surprising error's *cause* before blaming the app: the `500` was a
malformed JSON body from the test itself.
