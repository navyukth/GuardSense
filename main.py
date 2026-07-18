from CameraManager import CameraManager
import cv2

camera_Manager = CameraManager()

camera_Manager.add_cam(
    "front_gate",
    "rtsp://admin:admin%401234@192.168.0.141:554/cam/realmonitor?channel=1&subtype=1"
)
camera_Manager.add_cam(
    "side_gate",
    "rtsp://admin:admin%401234@192.168.0.141:554/cam/realmonitor?channel=2&subtype=1"
)
camera_Manager.add_cam(
    "front_door",
    "rtsp://admin:admin%401234@192.168.0.141:554/cam/realmonitor?channel=3&subtype=1"
)
camera_Manager.add_cam(
    "top",
    "rtsp://admin:admin%401234@192.168.0.141:554/cam/realmonitor?channel=4&subtype=1"
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