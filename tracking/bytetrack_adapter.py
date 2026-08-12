from tracking.tracker import Tracker
from DataClass.types import Track, TrackingResult, DetectionResult, Detection

import torch
from ultralytics.engine.results import Boxes
from ultralytics.trackers.byte_tracker import BYTETracker
from ultralytics.utils import YAML, IterableSimpleNamespace
from ultralytics.utils.checks import check_yaml

class ByteTrackAdapter(Tracker):

    def __init__(self, tracker_config="bytetrack.yaml"):
        # Find Ultralytics' built-in tracker YAML
        tracker_config = check_yaml(tracker_config)

        # Load YAML
        config = YAML.load(tracker_config)

        # Convert dictionary → namespace expected by BYTETracker
        args = IterableSimpleNamespace(**config)

        # Create ByteTrack
        self.tracker = BYTETracker(args)

    def update(self, detectionResult: DetectionResult) -> TrackingResult:

        detections = detectionResult.detections

        if len(detections) == 0:
            return TrackingResult(
                frame=detectionResult.frame,
                tracks=[]
            )

        data = []

        for detection in detections:

            x1, y1, x2, y2 = detection.bbox

            data.append([
                x1,
                y1,
                x2,
                y2,
                detection.confidence,
                detection.class_id
            ])

        data = torch.tensor(
            data,
            dtype=torch.float32
        )

        boxes = Boxes(
            data,
            detectionResult.frame.frame.shape[:2]
        )


        output = self.tracker.update(
            boxes,
            detectionResult.frame.frame
        )


        # print("BYTE TRACK OUTPUT:")
        # print(output)
        # print("SHAPE:", output.shape)

        tracks = []

        for track in output:

            x1, y1, x2, y2 = map(int, track[:4])

            track_id = int(track[4])
            confidence = float(track[5])
            class_id = int(track[6])

            detection = Detection(
                bbox=(x1, y1, x2, y2),
                class_id=class_id,
                confidence=confidence
            )

            tracks.append(
                Track(
                    track_id=track_id,
                    detection=detection
                )
            )

        return TrackingResult(
            frame=detectionResult.frame,
            tracks=tracks
        )