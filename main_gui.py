import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from gui.main_window import MainWindow
from modules.camera_manager import CameraManager


CAMERAS = {

    1: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=1&subtype=0",

    2: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=2&subtype=0",

    3: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=3&subtype=0",

    4: "rtsp://admin:admin%401234@192.168.0.140:554/cam/realmonitor?channel=4&subtype=0",

}


def main():

    app = QApplication(sys.argv)

    manager = CameraManager(CAMERAS)

    manager.start()

    window = MainWindow()

    window.show()

    timer = QTimer()

    def update():

        result = manager.update()

        window.update_cameras(
            result["frames"]
        )

        for event in result["events"]:

            window.add_event(
                event
            )

    timer.timeout.connect(update)

    timer.start(30)

    exit_code = app.exec()

    manager.stop()

    sys.exit(exit_code)


if __name__ == "__main__":

    main()