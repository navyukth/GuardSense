import cv2
import threading
import time


class Camera:

    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.cap = None
        self.frame = None
        self.running = False
        self.lock = threading.Lock()

    def connect(self):
        if self.cap is not None:
            self.cap.release()

        print("Connecting camera...")

        self.cap = cv2.VideoCapture(
            self.rtsp_url,
            cv2.CAP_FFMPEG
        )

        if not self.cap.isOpened():
            print("Connection failed.")
            return False

        print("Camera Connected.")

        return True

    def _reader(self):
        while self.running:
            if self.cap is None or not self.cap.isOpened():
                if not self.connect():
                    time.sleep(2)
                    continue
            
            success, frame = self.cap.read()

            if not success:
                print("Lost connection. Reconnecting...")
                self.connect()
                continue

            with self.lock:
                self.frame = frame

    def start(self):
        self.running = True
        threading.Thread(
            target=self._reader,
            daemon=True
        ).start()

    def read(self):
        with self.lock:
            if self.frame is None:
                return None

            return self.frame.copy()

    def stop(self):
        self.running = False
        if self.cap is not None:
            self.cap.release()