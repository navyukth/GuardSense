import cv2

from modules.camera import Camera
from modules.tracker import Tracker
from modules.track_manager import TrackManager
from modules.embedding_filter import EmbeddingFilter
from modules.osnet import OSNet
from modules.renderer import Renderer


class CameraWorker:

    def __init__(
        self,
        camera_id,
        rtsp_url,
        identity_manager,
        osnet,
        embedding_interval=10
    ):

        self.camera_id = camera_id

        self.identity_manager = identity_manager

        self.embedding_interval = embedding_interval

        self.frame_count = 0

        ####################################################
        # Pipeline
        ####################################################

        self.camera = Camera(rtsp_url)

        self.tracker = Tracker()

        self.track_manager = TrackManager()

        self.osnet = osnet

        self.embedding_filter = EmbeddingFilter(
            self.osnet
        )

    def start(self):

        self.camera.start()

    def stop(self):

        self.camera.stop()

    def update(self):

        frame = self.camera.read()

        if frame is None:
            return None

        self.frame_count += 1

        ####################################################
        # Detection
        ####################################################

        people = self.tracker.track(frame)

        state = self.track_manager.update(
            people
        )

        ####################################################
        # Lost Tracks
        ####################################################

        for track in state["lost_tracks"]:

            self.embedding_filter.clear(
                track["track_id"]
            )

        ####################################################
        # Re-ID
        ####################################################

        if self.frame_count % self.embedding_interval == 0:

            for person in state["people"]:

                track = person["track"]

                if not track["stable"]:
                    continue

                embedding = self.osnet.extract(
                    frame,
                    person["bbox"]
                )

                accepted = self.embedding_filter.add(
                    track["track_id"],
                    embedding
                )

                if not accepted:
                    continue

                embeddings = self.embedding_filter.get(
                    track["track_id"]
                )

                person_id = self.identity_manager.identify(
                    embeddings
                )

                if person_id is not None:

                    self.track_manager.set_person(
                        track["track_id"],
                        person_id
                    )

        ####################################################
        # Draw
        ####################################################

        frame = Renderer.draw(
            frame,
            state["people"]
        )

        return frame