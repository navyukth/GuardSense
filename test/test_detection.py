import cv2
import time
from DataClass.types import Frame
from detection.yolo_detector import YOLODetector


img = cv2.imread("Sample.jpg")

frame : Frame = Frame(
    camera_id = 1,
    timestamp = time.time(),
    frame = img
)

detector = YOLODetector(
        model_name = "yolov8n.pt",
        confidence = 0.75
    )

result = detector.detect(frame)

print(f"Camera Id: {result.frame.camera_id} \n TimeStamp: {result.frame.timestamp}")

for res in result.detections:
    print(f"Confidence: {res.confidence} Class_id: {res.class_id}")