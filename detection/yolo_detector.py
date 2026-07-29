from detection.detector import Detector
from ultralytics import YOLO
from DataClass.types import Frame,Detection,DetectionResult

class YOLODetector(Detector):
    def __init__(self,model_name : str,confidence : float):
        try:
            self.model = YOLO(model_name)
            self.confidence = confidence
        except Exception as e:
            print("e")

    def detect(self, frame: Frame) -> DetectionResult:

        output: DetectionResult = DetectionResult(frame,[])

        img = frame.frame

        model_output = self.model.predict(source=img, classes=[0],conf = self.confidence)

        for prediction in model_output:
            boxes = prediction.boxes
            for box in boxes:
                detection : Detection = Detection(
                    bbox = tuple(map(int,box.xyxy[0].tolist())),
                    class_id = int (box.cls[0].item()),
                    confidence = box.conf[0].item()
                )
                output.detections.append(detection)
        
        return output