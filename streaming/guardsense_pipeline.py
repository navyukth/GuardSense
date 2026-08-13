import os
import threading
import time

import cv2
import numpy as np

from camera.CameraManager import CameraManager
from DataClass.types import Frame
from detection.yolo_detector import YOLODetector
from tracking.bytetrack_adapter import ByteTrackAdapter
from embedding.osnet_embedder import OSNetEmbedder
from database.db import GuardSenseDB
from reid.reid_matcher import ReIDMatcher
from dotenv import load_dotenv

load_dotenv()

CAMERAS = {
    "front_door": os.environ["RTSP_FRONT_DOOR"],
    "front_gate": os.environ["RTSP_FRONT_GATE"],
    "side_gate":  os.environ["RTSP_SIDE_GATE"],
    "top":        os.environ["RTSP_TOP"],
}


class CameraWorker:
    """
    Runs the detect -> track -> embed -> reid loop for ONE camera,
    on its own background thread. Detector, embedder, and reid_matcher
    are shared across all cameras (passed in); the tracker is NOT shared,
    since track IDs must stay isolated per camera stream.
    """

    def __init__(
        self,
        camera_id,
        camera_manager,
        detector,
        embedder,
        reid_matcher,
        inference_lock,
        embedding_interval=5,
        target_fps=10
    ):

        self.camera_id = camera_id
        self.camera_manager = camera_manager
        self.detector = detector
        self.embedder = embedder
        self.reid_matcher = reid_matcher
        self.inference_lock = inference_lock

        self.target_fps = target_fps
        self.frame_interval = 1.0 / target_fps

        self.tracker = ByteTrackAdapter()

        self.embedding_interval = embedding_interval
        self.frame_count = 0

        self.track_to_person = {}

        self.latest_frame = None
        self.lock = threading.Lock()

        self.running = False
        self.thread = None

    def start(self):

        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._process_loop,
            daemon=True
        )
        self.thread.start()

        print(f"CameraWorker started: {self.camera_id}")

    def _process_loop(self):

        while self.running:

            loop_start = time.time()

            frame_image = self.camera_manager.get_frame(
                self.camera_id
            )

            if frame_image is None:
                print(f"[{self.camera_id}] Failed to get camera frame")
                time.sleep(0.01)
                continue

            frame = Frame(
                camera_id=self.camera_id,
                timestamp=time.time(),
                frame=frame_image
            )

            with self.inference_lock:
                detection_result = self.detector.detect(frame)

            tracking_result = self.tracker.update(detection_result)

            # -------------------------------------------------
            # OSNet embedding + ReID matching (throttled)
            # -------------------------------------------------

            self.frame_count += 1

            if self.frame_count % self.embedding_interval == 0:

                with self.inference_lock:
                    embedding_result = self.embedder.extract(tracking_result)

                if embedding_result.embeddings:
                    new_mappings = self.reid_matcher.match(
                        embedding_result,
                        track_hints=self.track_to_person
                    )
                    self.track_to_person.update(new_mappings)

            # -------------------------------------------------
            # Draw annotations
            # -------------------------------------------------

            output = frame_image.copy()

            for track in tracking_result.tracks:

                x1, y1, x2, y2 = map(int, track.detection.bbox)

                track_id = track.track_id

                person_id = self.track_to_person.get(track_id)

                label = (
                    f"Person #{person_id}"
                    if person_id is not None
                    else f"ID: {track_id} (identifying...)"
                )

                cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 0), 2)

                cv2.putText(
                    output,
                    label,
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2
                )

            with self.lock:
                self.latest_frame = output

            # -------------------------------------------------
            # Pace the loop to target_fps, so frame production
            # is steady instead of bursty/uncapped
            # -------------------------------------------------

            elapsed = time.time() - loop_start
            sleep_time = self.frame_interval - elapsed

            if sleep_time > 0:
                time.sleep(sleep_time)

    def get_latest_frame(self):

        with self.lock:

            if self.latest_frame is None:
                return None

            return self.latest_frame.copy()

    def stop(self):

        self.running = False

        if self.thread is not None:
            self.thread.join(timeout=2)

        print(f"CameraWorker stopped: {self.camera_id}")


class GuardSensePipeline:
    """
    Owns all camera workers. Detector, embedder, DB, and reid_matcher are
    created ONCE here and shared across every camera.
    """

    def __init__(self):

        self.camera_manager = CameraManager()

        for camera_id, rtsp_url in CAMERAS.items():
            self.camera_manager.add_cam(camera_id, rtsp_url)

        self.detector = YOLODetector(
            model_name="yolov8n.pt",
            confidence=0.3,
            device="cuda"
        )

        self.embedder = OSNetEmbedder(
            model_name="osnet_x1_0",
            device="cuda"
        )

        self.db = GuardSenseDB(
            path="guardsense.db",
            crops_dir="crops"
        )

        self.reid_matcher = ReIDMatcher(
            db=self.db,
            threshold=0.72
        )

        # Shared across all cameras — ensures only one camera's YOLO/OSNet
        # inference runs on the GPU at a time, avoiding both the model-fuse
        # race and GPU memory contention on limited VRAM.
        self.inference_lock = threading.Lock()

        self.workers = {
            camera_id: CameraWorker(
                camera_id=camera_id,
                camera_manager=self.camera_manager,
                detector=self.detector,
                embedder=self.embedder,
                reid_matcher=self.reid_matcher,
                inference_lock=self.inference_lock,
                embedding_interval=5,
                target_fps=10
            )
            for camera_id in CAMERAS
        }

    def start(self):

        for worker in self.workers.values():
            worker.start()

        print("GuardSense pipeline started (all cameras)")

    def get_latest_frame(self, camera_id):

        worker = self.workers.get(camera_id)

        if worker is None:
            return None

        return worker.get_latest_frame()

    def get_camera_ids(self):
        return list(self.workers.keys())

    def stop(self):

        for worker in self.workers.values():
            worker.stop()

        self.camera_manager.stop()

        print("GuardSense pipeline stopped")