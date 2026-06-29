import cv2

from modules.camera_manager import CameraManager


CAMERAS = {

    1: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=1&subtype=0",

    2: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=2&subtype=0",

    3: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=3&subtype=0",

    4: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=4&subtype=0",

}


def main():

    manager = CameraManager(CAMERAS)

    manager.start()

    try:

        while True:

            frames = manager.update()

            for camera_id, frame in frames.items():

                cv2.imshow(
                    f"Camera {camera_id}",
                    frame
                )

            if cv2.waitKey(1) == ord("q"):
                break

    finally:

        manager.stop()

        cv2.destroyAllWindows()


if __name__ == "__main__":

    main()