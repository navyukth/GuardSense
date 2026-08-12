from Camera.CameraManager import CameraManager
import cv2
import os
import threading
import time


load_dotenv()

RTSP_URL = os.environ["RTSP_URL"]

camera_Manager = CameraManager()

camera_Manager.add_cam(
    "front_gate",
    RTSP_URL
)
camera_Manager.add_cam(
    "side_gate",
    RTSP_URL
)
camera_Manager.add_cam(
    "front_door",
    RTSP_URL
)
camera_Manager.add_cam(
    "top",
    RTSP_URL
)


while True:
    front_gate = camera_Manager.get_frame("front_gate")
    side_gate = camera_Manager.get_frame("side_gate")
    front_door = camera_Manager.get_frame("front_door")
    top = camera_Manager.get_frame("top")

    if front_gate is not None:
        cv2.imshow("Front Gate", front_gate)

    if side_gate is not None:
        cv2.imshow("Side Gate", side_gate)

    if front_door is not None:
        cv2.imshow("Front Door", front_door)

    if top is not None:
        cv2.imshow("Top", top)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

camera_Manager.stop()
cv2.destroyAllWindows()