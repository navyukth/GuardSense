import os
import threading
import time

import cv2

from camera.CameraManager import CameraManager
from DataClass.types import Frame
from detection.yolo_detector import YOLODetector
from tracking.bytetrack_adapter import ByteTrackAdapter
from dotenv import load_dotenv

load_dotenv()

RTSP_URL = os.environ["RTSP_URL"]

class GuardSensePipeline:

    def __init__(self):

        self.camera_manager = CameraManager()

        self.camera_manager.add_cam(
            "front_door",
            RTSP_URL
        )

        self.detector = YOLODetector(
            model_name="yolov8n.pt",
            confidence=0.3
        )

        self.tracker = ByteTrackAdapter()

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
        print("GuardSense pipeline started")

    def _process_loop(self):

        while self.running:

            frame_image = self.camera_manager.get_frame(
                "front_door"
            )

            if frame_image is None:
                print("Failed to get camera frame")
                time.sleep(0.01)
                continue

            frame = Frame(
                camera_id="front_door",
                timestamp=time.time(),
                frame=frame_image
            )
            detection_result = self.detector.detect(
                frame
            )
            tracking_result = self.tracker.update(
                detection_result
            )

            output = frame_image.copy()

            for track in tracking_result.tracks:

                x1, y1, x2, y2 = map(
                    int,
                    track.detection.bbox
                )

                track_id = track.track_id

                confidence = (
                    track.detection.confidence
                )

                cv2.rectangle(
                    output,
                    (x1, y1),
                    (x2, y2),
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    output,
                    f"ID: {track_id}",
                    (x1, max(y1 - 10, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    output,
                    f"{confidence:.2f}",
                    (x1, y2 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1
                )


            with self.lock:
                self.latest_frame = output

    def get_latest_frame(self):

        with self.lock:

            if self.latest_frame is None:
                return None

            return self.latest_frame.copy()

    def stop(self):

        self.running = False

        if self.thread is not None:
            self.thread.join(timeout=2)

        self.camera_manager.stop()

        print("GuardSense pipeline stopped")