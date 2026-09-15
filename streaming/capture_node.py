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
REID_MATCH_THRESHOLD = float(os.environ.get("REID_MATCH_THRESHOLD", "0.6"))
REID_REFRESH_INTERVAL = float(os.environ.get("REID_REFRESH_INTERVAL", "15"))

# Where re-id crops get written, one JPEG per (camera, track) the first
# time that track is embedded. Gitignored - this fills up with real
# footage of people, it never belongs in the repo.
CROPS_DIR = os.environ.get("CROPS_DIR", "crops")

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
MAX_ALERTS = 100
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

        logger.info("Loading YOLODetector (%s, imgsz=%s, model=%s)...", INFERENCE_DEVICE, YOLO_IMGSZ, YOLO_MODEL)
        self.detector = YOLODetector(
            model_name=YOLO_MODEL, confidence=0.3, device=INFERENCE_DEVICE, imgsz=YOLO_IMGSZ
        )

        logger.info("Loading OSNetEmbedder (%s)...", INFERENCE_DEVICE)
        self.embedder = OSNetEmbedder(model_name="osnet_x1_0", device=INFERENCE_DEVICE)

        # One tracker per camera - track IDs must never cross feeds
        self.trackers = {cid: ByteTrackAdapter() for cid in self.camera_ids}

        # camera_id -> set of track_ids already alerted on
        self.seen_track_ids = {cid: set() for cid in self.camera_ids}

        self.matcher = IdentityMatcher(
            RELAY_HTTP_URL, RELAY_INGEST_TOKEN,
            threshold=REID_MATCH_THRESHOLD, refresh_interval=REID_REFRESH_INTERVAL
        )
        # (camera_id, track_id) -> (person_id, name) once live re-id matches
        self.track_identity = {}

        # (camera_id, track_id) -> alert dict, insertion order tracked
        # separately so a track matched to a name AFTER its alert already
        # fired can have that same alert's label updated in place instead
        # of leaving it stuck on "Person #<track_id>" forever.
        self.alerts_by_track = {}
        self.alert_order = []

        self.loop_count = 0
        self.started_at = time.time()

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
                self.save_crops(embedding_results)
                alerts_changed = self._match_identities(embedding_results)

        annotated = {}

        for tracking_result in tracking_results:
            camera_id = tracking_result.frame.camera_id
            image = tracking_result.frame.frame.copy()
            self._draw_annotations(image, camera_id, tracking_result)
            annotated[camera_id] = image

            seen = self.seen_track_ids[camera_id]
            for track in tracking_result.tracks:
                if track.track_id not in seen:
                    seen.add(track.track_id)
                    alerts_changed = True
                    self._add_alert(camera_id, track.track_id)

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
        _, name = self.track_identity.get(key, (None, None))

        self.alerts_by_track[key] = {
            "camera_id": camera_id,
            "person_id": track_id,
            "label": name or f"Person #{track_id}",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.alert_order.append(key)

    def current_alerts(self):
        # newest first, capped - self.alert_order only ever grows by
        # appending, so walking it in reverse is newest-to-oldest
        ordered = [self.alerts_by_track[k] for k in reversed(self.alert_order) if k in self.alerts_by_track]
        return ordered[:MAX_ALERTS]

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

                for embedding_result in embedding_results:
                    camera_id = embedding_result.frame.camera_id
                    for embedding in embedding_result.embeddings:
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
