import numpy as np

from detection.detector import Detector
from ultralytics import YOLO
from DataClass.types import Frame, Detection, DetectionResult


class YOLODetector(Detector):

    def __init__(self, model_name: str, confidence: float, device: str = "cuda"):

        try:
            self.model = YOLO(model_name)
            self.model.to(device)
            self.confidence = confidence
        except Exception as e:
            print(f"YOLODetector failed to load model '{model_name}': {e}")
            raise

        self._warmup()

    def _warmup(self):
        """
        Runs one dummy inference synchronously, right here in __init__,
        BEFORE this detector is shared across multiple camera threads.

        Ultralytics YOLO does a one-time internal 'fuse' step (merging
        batchnorm into conv layers) on its FIRST .predict() call, which
        mutates the model object. If multiple threads call .predict() for
        the first time at nearly the same moment, they race to fuse the
        same model and crash with:
            AttributeError: 'Conv' object has no attribute 'bn'

        Running one dummy predict here — single-threaded, before any
        CameraWorker threads exist — forces that one-time fuse to happen
        safely. All later calls, from any thread, skip the fuse step.
        """

        dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)

        self.model.predict(
            source=dummy_frame,
            classes=[0],
            conf=self.confidence,
            verbose=False
        )

        print("YOLODetector: warmup complete, model fused")

    def detect(self, frame: Frame) -> DetectionResult:
        return self.detect_batch([frame])[0]

    def detect_batch(self, frames: list[Frame]) -> list[DetectionResult]:
        """
        Runs ONE forward pass across frames from multiple cameras at once,
        instead of one .predict() call per camera. Ultralytics batches a
        list of images internally, so this is the "single model instance,
        one call covers every camera" path.
        """

        if not frames:
            return []

        images = [f.frame for f in frames]

        model_output = self.model.predict(
            source=images,
            classes=[0],
            conf=self.confidence,
            verbose=False
        )

        results = []

        for frame, prediction in zip(frames, model_output):

            output = DetectionResult(frame, [])

            for box in prediction.boxes:
                detection: Detection = Detection(
                    bbox=tuple(map(int, box.xyxy[0].tolist())),
                    class_id=int(box.cls[0].item()),
                    confidence=box.conf[0].item()
                )
                output.detections.append(detection)

            results.append(output)

        return results