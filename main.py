import cv2
import time

from modules.camera import Camera
from modules.tracker import Tracker
from modules.track_manager import TrackManager
from modules.osnet import OSNet
from modules.embedding_filter import EmbeddingFilter
from modules.identity_manager import IdentityManager
from modules.renderer import Renderer


RTSP_URL = (
    "rtsp://admin:admin%401234@192.168.0.140:554/"
    "cam/realmonitor?channel=3&subtype=0"
)

EMBEDDING_INTERVAL = 10      # Every 10 frames


def main():

    camera = Camera(RTSP_URL)
    camera.start()

    tracker = Tracker()

    track_manager = TrackManager()

    osnet = OSNet()

    embedding_filter = EmbeddingFilter(osnet)

    identity_manager = IdentityManager(osnet)

    frame_count = 0

    while True:

        frame = camera.read()

        if frame is None:
            continue

        frame_count += 1

        ####################################################
        # Detection + Tracking
        ####################################################

        people = tracker.track(frame)

        state = track_manager.update(people)

        ####################################################
        # Remove Lost Tracks
        ####################################################

        for track in state["lost_tracks"]:

            embedding_filter.clear(
                track["track_id"]
            )

        ####################################################
        # Re-Identification
        ####################################################

        if frame_count % EMBEDDING_INTERVAL == 0:

            for person in state["people"]:

                track = person["track"]

                if not track["stable"]:
                    continue

                embedding = osnet.extract(
                    frame,
                    person["bbox"]
                )

                accepted = embedding_filter.add(
                    track["track_id"],
                    embedding
                )

                if not accepted:
                    continue

                embeddings = embedding_filter.get(
                    track["track_id"]
                )

                person_id = identity_manager.identify(
                    embeddings
                )

                if person_id is not None:
                    track_manager.set_person(
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

        cv2.imshow(
            "GuardSense",
            frame
        )

        key = cv2.waitKey(1)

        if key == ord("q"):
            break

    camera.stop()

    cv2.destroyAllWindows()


if __name__ == "__main__":

    main()