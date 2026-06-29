from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QListWidget,
    QVBoxLayout
)

from PySide6.QtCore import Qt


class EventsWidget(QWidget):

    def __init__(self):

        super().__init__()

        layout = QVBoxLayout(self)

        title = QLabel("Events")

        title.setAlignment(Qt.AlignCenter)

        title.setStyleSheet("""
            font-size:18px;
            font-weight:bold;
        """)

        self.events = QListWidget()

        self.events.setStyleSheet("""

            QListWidget{
                background:#1f1f1f;
                border:2px solid #444;
                color:white;
                font-size:13px;
            }

        """)

        layout.addWidget(title)

        layout.addWidget(self.events)

    ####################################################

    def add_event(self, text):

        self.events.insertItem(
            0,
            text
        )

        while self.events.count() > 100:
            self.events.takeItem(100)