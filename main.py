import cv2
import numpy as np

from modules.camera_manager import CameraManager


CAMERAS = {

    1: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=1&subtype=0",

    2: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=2&subtype=0",

    3: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=3&subtype=0",

    4: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=4&subtype=0",

}


FRAME_WIDTH = 640
FRAME_HEIGHT = 360


def blank_frame():

    return np.zeros(
        (FRAME_HEIGHT, FRAME_WIDTH, 3),
        dtype=np.uint8
    )


def prepare_frame(camera_id, frame):

    if frame is None:

        frame = blank_frame()

    else:

        frame = cv2.resize(
            frame,
            (FRAME_WIDTH, FRAME_HEIGHT)
        )

    cv2.putText(
        frame,
        f"Camera {camera_id}",
        (15, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 255),
        2
    )

    return frame


def main():

    manager = CameraManager(CAMERAS)

    manager.start()

    try:

        while True:

            frames = manager.update()

            dashboard = []

            for camera_id in sorted(CAMERAS.keys()):

                dashboard.append(

                    prepare_frame(

                        camera_id,

                        frames.get(camera_id)

                    )

                )

            top = np.hstack((
                dashboard[0],
                dashboard[1]
            ))

            bottom = np.hstack((
                dashboard[2],
                dashboard[3]
            ))

            combined = np.vstack((
                top,
                bottom
            ))

            cv2.imshow(
                "GuardSense Dashboard",
                combined
            )

            key = cv2.waitKey(1)

            if key == ord("q"):
                break

    finally:

        manager.stop()

        cv2.destroyAllWindows()


if __name__ == "__main__":

    main()