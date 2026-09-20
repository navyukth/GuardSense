import logging
import threading
import time

import cv2

logger = logging.getLogger("guardsense.camera")

RECONNECT_DELAY = 2.0
MAX_CONSECUTIVE_FAILURES = 10


class _CameraReader:
    """
    Runs cap.read() in a tight loop on its own thread and keeps only the
    most recently decoded frame. CAP_PROP_BUFFERSIZE doesn't reliably
    limit the FFMPEG backend's internal RTSP buffer - if the consumer
    (YOLO on CPU, across 4 feeds) reads slower than the camera produces
    frames, that buffer backs up and get_frame() starts returning
    minutes-old video. Continuously draining the stream here, independent
    of how often/slowly get_frame() is called, keeps latency bounded to
    roughly one decode interval instead of accumulating forever.
    """

    def __init__(self, cam_id, src):
        self.src = src
        self.cam_id = cam_id
        self.lock = threading.Lock()
        self.latest_frame = None
        self.running = True

        # A camera that's unreachable at startup (DVR rebooting, cable out)
        # must not be dropped for good, or take the whole process down - the
        # read loop below keeps retrying it in the background.
        self.cap = self._open(src)
        if not self.cap.isOpened():
            logger.warning("'%s' not reachable yet, will keep retrying in the background", cam_id)

        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    @staticmethod
    def _open(src):
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _reconnect(self):
        was_up = self.latest_frame is not None
        if was_up:
            logger.warning("'%s' RTSP connection lost, reconnecting...", self.cam_id)

        self.cap.release()

        # Don't keep serving the last good frame as if it were live.
        with self.lock:
            self.latest_frame = None

        time.sleep(RECONNECT_DELAY)
        self.cap = self._open(self.src)

        if self.cap.isOpened():
            logger.info("'%s' reconnected", self.cam_id)
        elif was_up:
            logger.error("'%s' reconnect failed, will retry", self.cam_id)

    def _read_loop(self):
        failures = 0

        while self.running:
            ret, frame = self.cap.read()

            if not ret:
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    self._reconnect()
                    failures = 0
                continue

            failures = 0
            with self.lock:
                self.latest_frame = frame

    def get_frame(self):
        with self.lock:
            return self.latest_frame

    def stop(self):
        self.running = False
        self.thread.join(timeout=2)
        self.cap.release()


class CameraManager:
    def __init__(self):
        self.cameras = {}

    def add_cam(self, cam_id, src):
        self.cameras[cam_id] = _CameraReader(cam_id, src)

    def get_frame(self, cam_id):
        reader = self.cameras.get(cam_id)

        if reader is None:
            return None

        return reader.get_frame()

    def remove_came(self, cam_id):
        reader = self.cameras.pop(cam_id, None)

        if reader:
            reader.stop()

    def stop(self):
        for reader in self.cameras.values():
            reader.stop()

        self.cameras.clear()
