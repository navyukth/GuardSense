from ultralytics import YOLO

class YOLODetector:
    def __init__(self,model_path="models/yolov8n.pt",confidence=0.4):
        print("Loading YOLO model...")
        self.model = YOLO(model_path)
        self.confidence = confidence
        print("YOLO Ready.")

    def detect(self, frame):
        results = self.model(
            frame,
            classes=[0],
            conf=self.confidence,
            verbose=False
        )
        detections = []
        for box in results[0].boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append({
                "bbox": (
                    int(x1),
                    int(y1),
                    int(x2),
                    int(y2)
                ),
                "confidence": float(box.conf[0]),
                "class_id": int(box.cls[0]),
                "class_name": "person"
            })
        return detections