from camera.CameraManager import CameraManager
from DataClass.types import Frame, DetectionResult
from detection.yolo_detector import YOLODetector
from tracking.bytetrack_adapter import ByteTrackAdapter

from streaming.rtmp_publisher import RTMPPublisher

import time
import cv2

camera_Manager = CameraManager()

camera_Manager.add_cam(
    "front_gate",
    "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=1&subtype=0"
)

camera_Manager.add_cam(
    "side_gate",
    "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=2&subtype=0"
)

camera_Manager.add_cam(
    "front_door",
    "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=3&subtype=0"
)

camera_Manager.add_cam(
    "top",
    "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=4&subtype=0"
)

detector = YOLODetector(
    # model_name="yolov8n.pt",
    model_name="yolov8n.pt",
    confidence=0.3
)


tracker = ByteTrackAdapter()

front_door = camera_Manager.get_frame("front_door")

height, width = front_door.shape[:2]
    
publisher = RTMPPublisher(
    rtmp_url="rtmp://localhost:1935/LiveApp/guardsense",
    width=width,
    height=height,
    fps=25
)

while True:

    front_door = camera_Manager.get_frame("front_door")

    if front_door is None:
        print("Failed to get frame")
        continue

    frame = Frame(
        camera_id="front_door",
        timestamp=time.time(),
        frame=front_door
    )

    detectionResult: DetectionResult = detector.detect(frame)
    trackingResult = tracker.update(detectionResult)

    for track in trackingResult.tracks:

        x1, y1, x2, y2 = map(int, track.detection.bbox)

        cv2.rectangle(
            front_door,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            2
        )

        cv2.putText(
            front_door,
            f"ID: {track.track_id}",
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2
        )

        cv2.putText(
            front_door,
            f"{track.detection.confidence:.2f}",
            (x1, y2 + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1
        )
    
    publisher.write(front_door)

    cv2.imshow("ByteTrack Tracking", front_door)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break


publisher.close()
cv2.destroyAllWindows()