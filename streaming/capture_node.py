"""
Laptop-side capture node: owns every camera, runs ONE batched YOLO pass per
loop across all feeds, tracks each camera separately (ByteTrack state must
never mix identities between cameras), periodically re-embeds active tracks
with OSNet, draws the annotated frame, and pushes it + alerts/status over a
websocket into streaming/relay_server.py running on the Pi5.

Run with:
    python -m streaming.capture_node
"""

import asyncio
import collections
import json
import logging
import os
import struct
import threading
import time
import uuid

import cv2
import numpy as np
import websockets
from dotenv import load_dotenv

from camera.CameraManager import CameraManager
from DataClass.types import Frame
from detection.yolo_detector import YOLODetector
from embedding.osnet_embedder import OSNetEmbedder
from tracking.bytetrack_adapter import ByteTrackAdapter
from streaming.identity_matcher import IdentityMatcher


load_dotenv()

logger = logging.getLogger("guardsense.capture")


class _RingBufferLogHandler(logging.Handler):
    """
    Queues recent log records (thread-safe - CameraManager's reader
    threads log through this too) for the main loop to periodically drain
    and ship up to the relay's Logs page. Lets you see what
    capture_node.py is doing from the browser instead of needing SSH
    access to whichever machine (laptop or Pi5) happens to be running it.
    """

    def __init__(self, maxlen=500):
        super().__init__()
        self.pending = collections.deque(maxlen=maxlen)
        # NOT self.lock - logging.Handler already uses that name internally
        # (handle() -> acquire() -> emit() locks self.lock before calling
        # us), so reusing it here self-deadlocks the first log call: the
        # base class acquires it, then our emit() tries to acquire the
        # same non-reentrant lock again on the same thread.
        self.buffer_lock = threading.Lock()

    def emit(self, record):
        entry = {
            "level": record.levelname,
            "message": self.format(record),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
        }
        with self.buffer_lock:
            self.pending.append(entry)

    def drain(self):
        with self.buffer_lock:
            entries = list(self.pending)
            self.pending.clear()
        return entries


_log_buffer_handler = _RingBufferLogHandler()
_log_buffer_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
logging.getLogger("guardsense").addHandler(_log_buffer_handler)

RELAY_URL = os.environ["RELAY_URL"]
RELAY_INGEST_TOKEN = os.environ["RELAY_INGEST_TOKEN"]
# Plain-HTTP base the relay also serves on (same host/port as RELAY_URL's
# ws://.../ws/ingest, just a different scheme+path) - used for the live
# re-id matcher's periodic GET, not the persistent ingest websocket.
RELAY_HTTP_URL = os.environ.get(
    "RELAY_HTTP_URL",
    RELAY_URL.replace("ws://", "http://").replace("wss://", "https://").rsplit("/ws/ingest", 1)[0]
)
INFERENCE_DEVICE = os.environ.get("INFERENCE_DEVICE", "cpu")
YOLO_IMGSZ = int(os.environ.get("YOLO_IMGSZ", "640"))
YOLO_MODEL = os.environ.get("YOLO_MODEL", "yolov8n.pt")
# 0.3 (the old default) let through a lot of bikes/shadows/reflections
# misdetected as "person" - raised the floor since low-imgsz inference
# (320px) is already more error-prone on ambiguous shapes.
YOLO_CONFIDENCE = float(os.environ.get("YOLO_CONFIDENCE", "0.5"))
REID_MATCH_THRESHOLD = float(os.environ.get("REID_MATCH_THRESHOLD", "0.6"))
REID_REFRESH_INTERVAL = float(os.environ.get("REID_REFRESH_INTERVAL", "15"))

# Where re-id crops get written, one JPEG per (camera, track) the first
# time that track is embedded. Gitignored - this fills up with real
# footage of people, it never belongs in the repo.
CROPS_DIR = os.environ.get("CROPS_DIR", "crops")

# Local debug copy of every embedded crop. Off by default: the relay already
# keeps the crops that matter, and this quietly duplicated every one of them
# (including all the ones never sent) onto the Pi's SD card.
SAVE_LOCAL_CROPS = os.environ.get("SAVE_LOCAL_CROPS", "false").lower() in ("1", "true", "yes")

# Which crops are worth sending to the relay at all. A person standing in
# view produces a crop every embedding cycle (~1/sec) - thousands of near
# identical images per day per person. These cap that at the source.
CROP_MIN_HEIGHT = int(os.environ.get("CROP_MIN_HEIGHT", "60"))
CROP_MIN_WIDTH = int(os.environ.get("CROP_MIN_WIDTH", "25"))
CROPS_PER_TRACK = int(os.environ.get("CROPS_PER_TRACK", "8"))                    # unrecognised person
CROPS_PER_TRACK_MATCHED = int(os.environ.get("CROPS_PER_TRACK_MATCHED", "4"))    # already-named person
CROP_MIN_INTERVAL = float(os.environ.get("CROP_MIN_INTERVAL", "10.0"))            # seconds between crops of one track

# camera_id -> env var holding its RTSP URL
CAMERA_ENV_MAP = {
    "front_door": "RTSP_FRONT_DOOR",
    "front_gate": "RTSP_FRONT_GATE",
    "side_gate": "RTSP_SIDE_GATE",
    "top": "RTSP_TOP",
}

EMBEDDING_INTERVAL = 5       # embed every Nth loop
STATUS_INTERVAL = 5.0        # seconds between status pushes
LOG_FLUSH_INTERVAL = 2.0     # seconds between shipping new log lines
JPEG_QUALITY = 80
MAX_ALERTS = 100             # alerts sent to the relay per update
ALERT_MEMORY = 500           # alerts kept in memory here (still open to label updates)
TRACK_STATE_TTL = 3600       # forget a track's bookkeeping after this long unseen
RECONNECT_DELAY = 3.0

BOX_COLOR = (0, 200, 0)
BOX_THICKNESS = 2


# Wire protocol shared with streaming/relay_server.py's ws_ingest -
# every binary message starts with a 1-byte type tag.
MSG_TYPE_FRAME = 0
MSG_TYPE_CROP = 1


def encode_frame_message(camera_id, jpeg_bytes):
    id_bytes = camera_id.encode("utf-8")
    return bytes([MSG_TYPE_FRAME, len(id_bytes)]) + id_bytes + jpeg_bytes


def encode_crop_message(camera_id, track_id, embedding, jpeg_bytes, person_id=None):
    id_bytes = camera_id.encode("utf-8")
    embedding = np.asarray(embedding, dtype=np.float32)

    return (
        bytes([MSG_TYPE_CROP, len(id_bytes)])
        + id_bytes
        + struct.pack(">I", track_id)
        + struct.pack(">H", embedding.shape[0])
        + embedding.tobytes()
        + struct.pack(">i", person_id if person_id is not None else -1)
        + jpeg_bytes
    )


class CaptureNode:

    def __init__(self):
        self.camera_manager = CameraManager()
        self.camera_ids = []

        for camera_id, env_key in CAMERA_ENV_MAP.items():
            url = os.environ.get(env_key)
            if not url:
                continue
            try:
                self.camera_manager.add_cam(camera_id, url)
                self.camera_ids.append(camera_id)
                logger.info("Connected camera '%s'", camera_id)
            except Exception as e:
                logger.error("Failed to connect camera '%s': %s", camera_id, e)

        if not self.camera_ids:
            raise RuntimeError("No cameras connected - check RTSP_* env vars")

        logger.info(
            "Loading YOLODetector (%s, imgsz=%s, model=%s, confidence=%s)...",
            INFERENCE_DEVICE, YOLO_IMGSZ, YOLO_MODEL, YOLO_CONFIDENCE
        )
        self.detector = YOLODetector(
            model_name=YOLO_MODEL, confidence=YOLO_CONFIDENCE, device=INFERENCE_DEVICE, imgsz=YOLO_IMGSZ
        )

        logger.info("Loading OSNetEmbedder (%s)...", INFERENCE_DEVICE)
        self.embedder = OSNetEmbedder(model_name="osnet_x1_0", device=INFERENCE_DEVICE)

        # One tracker per camera - track IDs must never cross feeds
        self.trackers = {cid: ByteTrackAdapter() for cid in self.camera_ids}

        # camera_id -> {track_id: last_seen_at} for tracks already alerted on.
        # A dict (not a set) so entries for long-gone tracks can be pruned -
        # a plain set of every track_id ever seen grew forever.
        self.seen_track_ids = {cid: {} for cid in self.camera_ids}

        self.matcher = IdentityMatcher(
            RELAY_HTTP_URL, RELAY_INGEST_TOKEN,
            threshold=REID_MATCH_THRESHOLD, refresh_interval=REID_REFRESH_INTERVAL
        )
        # (camera_id, track_id) -> (person_id, name) once live re-id matches
        self.track_identity = {}

        # (camera_id, track_id) -> alert dict, oldest first. Keyed (not just
        # appended) so a track matched to a name AFTER its alert already
        # fired can have that same alert's label updated in place instead
        # of leaving it stuck on "Person #<track_id>" forever. Capped at
        # ALERT_MEMORY entries - the relay persists the full history in
        # SQLite, this is just the recent window still open to updates.
        self.alerts_by_track = collections.OrderedDict()

        # (camera_id, track_id) -> [crops_sent, last_sent_at] - see _should_send_crop
        self.crop_sent = {}

        self.loop_count = 0
        self.started_at = time.time()

        if SAVE_LOCAL_CROPS:
            os.makedirs(CROPS_DIR, exist_ok=True)
            for camera_id in self.camera_ids:
                os.makedirs(os.path.join(CROPS_DIR, camera_id), exist_ok=True)

    def grab_frames(self):
        frames = []
        for camera_id in self.camera_ids:
            img = self.camera_manager.get_frame(camera_id)
            if img is None:
                continue
            frames.append(Frame(camera_id=camera_id, timestamp=time.time(), frame=img))
        return frames

    def process(self, frames):
        """One batched detect pass, then per-camera tracking, then periodic
        pooled embedding (matched against known people via live re-id).
        Returns (annotated_by_camera, alerts_changed, embedding_results) -
        embedding_results is [] on loops that skip embedding, and
        alerts_changed is True if self.alerts_by_track needs re-pushing."""

        detection_results = self.detector.detect_batch(frames)

        tracking_results = []
        for detection_result in detection_results:
            camera_id = detection_result.frame.camera_id
            tracker = self.trackers[camera_id]
            tracking_results.append(tracker.update(detection_result))

        embedding_results = []
        alerts_changed = False

        if self.loop_count % EMBEDDING_INTERVAL == 0:
            non_empty = [tr for tr in tracking_results if tr.tracks]
            if non_empty:
                embedding_results = self.embedder.extract_batch(non_empty)
                if SAVE_LOCAL_CROPS:
                    self.save_crops(embedding_results)
                alerts_changed = self._match_identities(embedding_results)

        annotated = {}

        for tracking_result in tracking_results:
            camera_id = tracking_result.frame.camera_id
            image = tracking_result.frame.frame.copy()
            self._draw_annotations(image, camera_id, tracking_result)
            annotated[camera_id] = image

            seen = self.seen_track_ids[camera_id]
            now = time.time()
            for track in tracking_result.tracks:
                if track.track_id not in seen:
                    alerts_changed = True
                    self._add_alert(camera_id, track.track_id)
                seen[track.track_id] = now

        return annotated, alerts_changed, embedding_results

    def _match_identities(self, embedding_results):
        """Matches each embedded track against known people and records the
        result in self.track_identity. Returns True if any match changed an
        already-fired alert's label (so it should be re-pushed)."""

        changed = False

        for embedding_result in embedding_results:
            camera_id = embedding_result.frame.camera_id
            for embedding in embedding_result.embeddings:
                key = (camera_id, embedding.track_id)
                person_id, name = self.matcher.match(embedding.vector)

                if person_id is None:
                    continue

                if self.track_identity.get(key) != (person_id, name):
                    self.track_identity[key] = (person_id, name)
                    if key in self.alerts_by_track:
                        self.alerts_by_track[key]["label"] = name
                        self.alerts_by_track[key]["person_id"] = person_id
                        changed = True

        return changed

    def _draw_annotations(self, image, camera_id, tracking_result):
        for track in tracking_result.tracks:
            x1, y1, x2, y2 = track.detection.bbox
            _, name = self.track_identity.get((camera_id, track.track_id), (None, None))
            label = f"{name} {track.detection.confidence:.2f}" if name else \
                f"#{track.track_id} {track.detection.confidence:.2f}"

            cv2.rectangle(image, (x1, y1), (x2, y2), BOX_COLOR, BOX_THICKNESS)
            cv2.putText(
                image, label, (x1, max(y1 - 8, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, BOX_COLOR, 1, cv2.LINE_AA
            )

    def _add_alert(self, camera_id, track_id):
        key = (camera_id, track_id)
        person_id, name = self.track_identity.get(key, (None, None))

        self.alerts_by_track[key] = {
            # stable id so the relay can upsert this alert into SQLite (and
            # update its label later) - track_id alone isn't unique across
            # capture restarts
            "id": uuid.uuid4().hex,
            "ts": time.time(),
            "camera_id": camera_id,
            # track_id is the raw ByteTrack ID (keeps climbing forever,
            # resets to nothing meaningful - it's NOT a count of people).
            # person_id is the real identity_store ID, only set once
            # matched to a known person; null until then.
            "track_id": track_id,
            "person_id": person_id,
            "label": name or f"Person #{track_id}",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        while len(self.alerts_by_track) > ALERT_MEMORY:
            self.alerts_by_track.popitem(last=False)

    def current_alerts(self):
        # newest first (the OrderedDict is oldest-first), capped
        return list(reversed(self.alerts_by_track.values()))[:MAX_ALERTS]

    def _should_send_crop(self, camera_id, embedding, now):
        """Decides whether this crop is worth shipping to the relay. Embedding
        still runs for every track (that's what drives live re-id and
        alerts) - this only limits what gets *stored*:
          - too small to carry identity signal -> skip
          - per-track cap (lower once the person is already recognised,
            since we already have plenty of reference crops for them)
          - minimum gap between two crops of the same track, so the ones
            kept are spread over time instead of consecutive near-copies"""

        crop_height, crop_width = embedding.crop.shape[:2]
        if crop_height < CROP_MIN_HEIGHT or crop_width < CROP_MIN_WIDTH:
            return False

        key = (camera_id, embedding.track_id)
        matched = key in self.track_identity
        cap = CROPS_PER_TRACK_MATCHED if matched else CROPS_PER_TRACK

        sent, last_sent_at = self.crop_sent.get(key, (0, 0.0))
        if sent >= cap or now - last_sent_at < CROP_MIN_INTERVAL:
            return False

        self.crop_sent[key] = (sent + 1, now)
        return True

    def _prune_track_state(self, now):
        """Tracks come and go forever (the ID counter only ever climbs), so
        every per-track dict has to shed entries for tracks that are gone or
        it grows without bound. ByteTrack drops a lost track within seconds
        and never reuses its ID, so an hour of silence means it's dead."""

        for seen in self.seen_track_ids.values():
            for track_id in [t for t, last in seen.items() if now - last > TRACK_STATE_TTL]:
                del seen[track_id]

        live = {(cid, tid) for cid, seen in self.seen_track_ids.items() for tid in seen}

        for key in [k for k in self.track_identity if k not in live]:
            del self.track_identity[key]

        for key in [k for k in self.crop_sent if k not in live]:
            del self.crop_sent[key]

    def save_crops(self, embedding_results):
        for embedding_result in embedding_results:
            camera_id = embedding_result.frame.camera_id
            for embedding in embedding_result.embeddings:
                filename = f"track{embedding.track_id}_{int(time.time() * 1000)}.jpg"
                path = os.path.join(CROPS_DIR, camera_id, filename)
                cv2.imwrite(path, embedding.crop)

    def status_payload(self):
        return {
            "device": INFERENCE_DEVICE,
            "camera_ids": self.camera_ids,
            "loop_count": self.loop_count,
        }

    async def run(self):
        while True:
            try:
                await self._run_connected()
            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                logger.warning("Relay connection lost (%s), reconnecting in %ss...", e, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _run_connected(self):
        url = f"{RELAY_URL}?token={RELAY_INGEST_TOKEN}"
        logger.info("Connecting to relay at %s...", RELAY_URL)

        async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
            logger.info("Connected to relay.")
            last_status_at = 0.0
            last_log_at = 0.0

            while True:
                loop_start = time.time()

                # requests.get() is blocking - run it off the event loop so
                # a slow/unreachable relay never stalls frame delivery
                await asyncio.to_thread(self.matcher.refresh_if_due)

                frames = self.grab_frames()
                if not frames:
                    await asyncio.sleep(0.05)
                    continue

                annotated, alerts_changed, embedding_results = self.process(frames)

                for camera_id, image in annotated.items():
                    ok, buf = cv2.imencode(
                        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                    )
                    if not ok:
                        continue
                    await ws.send(encode_frame_message(camera_id, buf.tobytes()))

                now = time.time()
                if self.loop_count % 600 == 0:
                    self._prune_track_state(now)

                for embedding_result in embedding_results:
                    camera_id = embedding_result.frame.camera_id
                    for embedding in embedding_result.embeddings:
                        if not self._should_send_crop(camera_id, embedding, now):
                            continue
                        ok, buf = cv2.imencode(
                            ".jpg", embedding.crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
                        )
                        if not ok:
                            continue
                        person_id, _ = self.track_identity.get(
                            (camera_id, embedding.track_id), (None, None)
                        )
                        await ws.send(encode_crop_message(
                            camera_id, embedding.track_id, embedding.vector, buf.tobytes(),
                            person_id=person_id
                        ))

                if alerts_changed:
                    await ws.send(json.dumps({"type": "alerts", "data": self.current_alerts()}))

                if loop_start - last_status_at >= STATUS_INTERVAL:
                    await ws.send(json.dumps({"type": "status", "data": self.status_payload()}))
                    last_status_at = loop_start

                if loop_start - last_log_at >= LOG_FLUSH_INTERVAL:
                    new_logs = _log_buffer_handler.drain()
                    if new_logs:
                        await ws.send(json.dumps({"type": "logs", "data": new_logs}))
                    last_log_at = loop_start

                self.loop_count += 1

    def stop(self):
        self.camera_manager.stop()


async def main():
    node = CaptureNode()
    try:
        await node.run()
    finally:
        node.stop()


if __name__ == "__main__":
    asyncio.run(main())
