from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGridLayout
)

from PySide6.QtCore import Qt

from gui.widgets.camera_widget import CameraWidget
from gui.widgets.events_widget import EventsWidget


class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle("GuardSense")

        self.resize(1700, 900)

        ####################################################
        # Central Widget
        ####################################################

        central = QWidget()

        self.setCentralWidget(central)

        self.setStyleSheet("""

            QMainWindow{
                background:#202124;
            }

            QWidget{
                background:#202124;
                color:white;
            }

            QPushButton{
                background:#2d2d2d;
                color:white;
                border:none;
                padding:10px;
                font-size:14px;
                border-radius:6px;
            }

            QPushButton:hover{
                background:#3d3d3d;
            }

        """)

        main_layout = QVBoxLayout(central)

        ####################################################
        # Top Bar
        ####################################################

        top = QHBoxLayout()

        title = QLabel("GuardSense")

        title.setStyleSheet("""

            font-size:30px;
            font-weight:bold;

        """)

        self.status = QLabel("🟢 Connected")

        self.status.setStyleSheet("""

            font-size:16px;

        """)

        top.addWidget(title)

        top.addStretch()

        top.addWidget(self.status)

        ####################################################
        # Dashboard
        ####################################################

        dashboard = QWidget()

        dashboard_layout = QHBoxLayout(dashboard)

        ####################################################
        # Camera Grid
        ####################################################

        camera_area = QWidget()

        grid = QGridLayout(camera_area)

        grid.setSpacing(10)

        self.camera1 = CameraWidget("Camera 1")

        self.camera2 = CameraWidget("Camera 2")

        self.camera3 = CameraWidget("Camera 3")

        self.camera4 = CameraWidget("Camera 4")

        grid.addWidget(self.camera1, 0, 0)

        grid.addWidget(self.camera2, 0, 1)

        grid.addWidget(self.camera3, 1, 0)

        grid.addWidget(self.camera4, 1, 1)

        ####################################################
        # Events Panel
        ####################################################

        self.events = EventsWidget()

        dashboard_layout.addWidget(
            camera_area,
            stretch=4
        )

        dashboard_layout.addWidget(
            self.events,
            stretch=1
        )

        ####################################################
        # Bottom Navigation
        ####################################################

        bottom = QHBoxLayout()

        self.dashboard_btn = QPushButton("Dashboard")

        self.people_btn = QPushButton("People")

        self.events_btn = QPushButton("Events")

        self.zones_btn = QPushButton("Zones")

        self.settings_btn = QPushButton("Settings")

        bottom.addWidget(self.dashboard_btn)

        bottom.addWidget(self.people_btn)

        bottom.addWidget(self.events_btn)

        bottom.addWidget(self.zones_btn)

        bottom.addWidget(self.settings_btn)

        ####################################################
        # Assemble
        ####################################################

        main_layout.addLayout(top)

        main_layout.addWidget(
            dashboard,
            stretch=1
        )

        main_layout.addLayout(bottom)

    ####################################################
    # Camera Updates
    ####################################################

    def update_cameras(
        self,
        frames
    ):

        widgets = {

            1: self.camera1,

            2: self.camera2,

            3: self.camera3,

            4: self.camera4

        }

        for camera_id, widget in widgets.items():

            widget.update_frame(

                frames.get(camera_id)

            )

    ####################################################
    # Events
    ####################################################

    def add_event(
        self,
        text
    ):

        self.events.add_event(
            text
        )