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
import json
import os
import struct
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


load_dotenv()

RELAY_URL = os.environ["RELAY_URL"]
RELAY_INGEST_TOKEN = os.environ["RELAY_INGEST_TOKEN"]
INFERENCE_DEVICE = os.environ.get("INFERENCE_DEVICE", "cpu")

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
JPEG_QUALITY = 80
MAX_ALERTS = 100
RECONNECT_DELAY = 3.0

BOX_COLOR = (0, 200, 0)
BOX_THICKNESS = 2


def draw_annotations(image, tracking_result):
    for track in tracking_result.tracks:
        x1, y1, x2, y2 = track.detection.bbox
        cv2.rectangle(image, (x1, y1), (x2, y2), BOX_COLOR, BOX_THICKNESS)
        label = f"#{track.track_id} {track.detection.confidence:.2f}"
        cv2.putText(
            image, label, (x1, max(y1 - 8, 0)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, BOX_COLOR, 1, cv2.LINE_AA
        )
    return image


# Wire protocol shared with streaming/relay_server.py's ws_ingest -
# every binary message starts with a 1-byte type tag.
MSG_TYPE_FRAME = 0
MSG_TYPE_CROP = 1


def encode_frame_message(camera_id, jpeg_bytes):
    id_bytes = camera_id.encode("utf-8")
    return bytes([MSG_TYPE_FRAME, len(id_bytes)]) + id_bytes + jpeg_bytes


def encode_crop_message(camera_id, track_id, embedding, jpeg_bytes):
    id_bytes = camera_id.encode("utf-8")
    embedding = np.asarray(embedding, dtype=np.float32)

    return (
        bytes([MSG_TYPE_CROP, len(id_bytes)])
        + id_bytes
        + struct.pack(">I", track_id)
        + struct.pack(">H", embedding.shape[0])
        + embedding.tobytes()
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
                print(f"Connected camera '{camera_id}'")
            except Exception as e:
                print(f"Failed to connect camera '{camera_id}': {e}")

        if not self.camera_ids:
            raise RuntimeError("No cameras connected - check RTSP_* env vars")

        print(f"Loading YOLODetector ({INFERENCE_DEVICE})...")
        self.detector = YOLODetector(
            model_name="yolov8n.pt", confidence=0.3, device=INFERENCE_DEVICE
        )

        print(f"Loading OSNetEmbedder ({INFERENCE_DEVICE})...")
        self.embedder = OSNetEmbedder(model_name="osnet_x1_0", device=INFERENCE_DEVICE)

        # One tracker per camera - track IDs must never cross feeds
        self.trackers = {cid: ByteTrackAdapter() for cid in self.camera_ids}

        # camera_id -> set of track_ids already alerted on
        self.seen_track_ids = {cid: set() for cid in self.camera_ids}

        self.alerts = []
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
        pooled embedding. Returns (annotated_by_camera, new_alerts,
        embedding_results) - embedding_results is [] on loops that skip
        embedding."""

        detection_results = self.detector.detect_batch(frames)

        tracking_results = []
        for detection_result in detection_results:
            camera_id = detection_result.frame.camera_id
            tracker = self.trackers[camera_id]
            tracking_results.append(tracker.update(detection_result))

        embedding_results = []
        if self.loop_count % EMBEDDING_INTERVAL == 0:
            non_empty = [tr for tr in tracking_results if tr.tracks]
            if non_empty:
                embedding_results = self.embedder.extract_batch(non_empty)
                self.save_crops(embedding_results)

        annotated = {}
        new_alerts = []

        for tracking_result in tracking_results:
            camera_id = tracking_result.frame.camera_id
            image = tracking_result.frame.frame.copy()
            draw_annotations(image, tracking_result)
            annotated[camera_id] = image

            seen = self.seen_track_ids[camera_id]
            for track in tracking_result.tracks:
                if track.track_id not in seen:
                    seen.add(track.track_id)
                    new_alerts.append({
                        "camera_id": camera_id,
                        "person_id": track.track_id,
                        "label": f"Person #{track.track_id}",
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })

        return annotated, new_alerts, embedding_results

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
                print(f"Relay connection lost ({e}), reconnecting in {RECONNECT_DELAY}s...")
                await asyncio.sleep(RECONNECT_DELAY)

    async def _run_connected(self):
        url = f"{RELAY_URL}?token={RELAY_INGEST_TOKEN}"
        print(f"Connecting to relay at {RELAY_URL}...")

        async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
            print("Connected to relay.")
            last_status_at = 0.0

            while True:
                loop_start = time.time()

                frames = self.grab_frames()
                if not frames:
                    await asyncio.sleep(0.05)
                    continue

                annotated, new_alerts, embedding_results = self.process(frames)

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
                        await ws.send(encode_crop_message(
                            camera_id, embedding.track_id, embedding.vector, buf.tobytes()
                        ))

                if new_alerts:
                    self.alerts = (new_alerts + self.alerts)[:MAX_ALERTS]
                    await ws.send(json.dumps({"type": "alerts", "data": self.alerts}))

                if loop_start - last_status_at >= STATUS_INTERVAL:
                    await ws.send(json.dumps({"type": "status", "data": self.status_payload()}))
                    last_status_at = loop_start

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
