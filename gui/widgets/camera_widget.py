from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout
)

from PySide6.QtGui import (
    QImage,
    QPixmap
)

from PySide6.QtCore import Qt

import cv2


class CameraWidget(QWidget):

    def __init__(
        self,
        camera_name="Camera"
    ):

        super().__init__()

        layout = QVBoxLayout(self)

        ####################################################
        # Camera Title
        ####################################################

        self.title = QLabel(camera_name)

        self.title.setAlignment(Qt.AlignCenter)

        self.title.setStyleSheet("""

            font-size:18px;
            font-weight:bold;

        """)

        ####################################################
        # Video Area
        ####################################################

        self.video = QLabel()

        self.video.setMinimumSize(640, 360)

        self.video.setAlignment(Qt.AlignCenter)

        self.video.setText("No Signal")

        self.video.setStyleSheet("""

            background:#111111;

            border:2px solid #444;

            color:gray;

            font-size:18px;

        """)

        layout.addWidget(self.title)

        layout.addWidget(self.video)

    ####################################################
    # Update Frame
    ####################################################

    def update_frame(
        self,
        frame
    ):

        if frame is None:

            self.video.setText("No Signal")

            return

        rgb = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        h, w, ch = rgb.shape

        image = QImage(
            rgb.data,
            w,
            h,
            ch * w,
            QImage.Format_RGB888
        )

        pixmap = QPixmap.fromImage(
            image
        )

        self.video.setPixmap(

            pixmap.scaled(

                self.video.size(),

                Qt.KeepAspectRatio,

                Qt.SmoothTransformation

            )

        )