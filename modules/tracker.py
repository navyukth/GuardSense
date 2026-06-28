from ultralytics import YOLO


class Tracker:

    def __init__(
        self,
        model_path="models/yolov8n.pt",
        confidence=0.4,
        tracker_config="bytetrack.yaml"
    ):
        print("Loading YOLO + ByteTrack...")
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.tracker_config = tracker_config
        print("Tracker Ready.")

    def track(self, frame):
        results = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker_config,
            classes=[0],
            conf=self.confidence,
            verbose=False
        )

        tracked_people = []
        boxes = results[0].boxes
        if boxes is None:
            return tracked_people
        for box in boxes:
            if box.id is None:
                continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            tracked_people.append({
                "id": int(box.id.item()),
                "bbox": (
                    int(x1),
                    int(y1),
                    int(x2),
                    int(y2)
                ),

                "confidence": float(box.conf.item()),
                "class_id": int(box.cls.item()),
                "class_name": "person"
            })

        return tracked_people