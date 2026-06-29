from modules.camera_worker import CameraWorker
from modules.osnet import OSNet
from modules.identity_manager import IdentityManager


class CameraManager:

    def __init__(self, cameras):

        """
        cameras = {
            1: "rtsp://....channel=1...",
            2: "rtsp://....channel=2...",
            3: "rtsp://....channel=3...",
            4: "rtsp://....channel=4..."
        }
        """

        ####################################################
        # Shared Modules
        ####################################################

        self.osnet = OSNet()

        self.identity_manager = IdentityManager(
            self.osnet
        )

        ####################################################
        # Camera Workers
        ####################################################

        self.workers = {}

        for camera_id, url in cameras.items():

            self.workers[camera_id] = CameraWorker(

                camera_id=camera_id,

                rtsp_url=url,

                identity_manager=self.identity_manager,

                osnet=self.osnet

            )

    ########################################################

    def start(self):

        for worker in self.workers.values():

            worker.start()

    ########################################################

    def stop(self):

        for worker in self.workers.values():

            worker.stop()

    ########################################################

    def update(self):

        frames = {}

        events = []

        for camera_id, worker in self.workers.items():

            result = worker.update()

            if result is None:
                continue

            if result["frame"] is not None:

                frames[camera_id] = result["frame"]

            events.extend(
                result["events"]
            )

        return {

            "frames": frames,

            "events": events

        }