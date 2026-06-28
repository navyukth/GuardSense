import time


class TrackManager:

    def __init__(
        self,
        stable_frames=15,
        lost_timeout=3.0
    ):

        self.tracks = {}

        self.stable_frames = stable_frames
        self.lost_timeout = lost_timeout

    def update(self, tracked_people):

        current_time = time.time()
        active_tracks = set()
        stable_tracks = []
        new_stable_tracks = []
        lost_tracks = []

        for person in tracked_people:
            track_id = person["id"]
            active_tracks.add(track_id)
            if track_id not in self.tracks:
                self.tracks[track_id] = {
                    "track_id": track_id,
                    "age": 0,
                    "stable": True,
                    "identified": False,
                    "person_id": None,
                    "last_seen": current_time
                }

            track = self.tracks[track_id]

            track["age"] += 1

            track["last_seen"] = current_time
            if (
                not track["stable"]
                and track["age"] >= self.stable_frames
            ):

                track["stable"] = True
                new_stable_tracks.append(track)
            if track["stable"]:
                stable_tracks.append(track)
            person["track"] = track

        for track_id in list(self.tracks.keys()):
            if track_id in active_tracks:
                continue

            track = self.tracks[track_id]
            if current_time - track["last_seen"] > self.lost_timeout:
                lost_tracks.append(track)
                del self.tracks[track_id]
        return {
            "people": tracked_people,
            "stable_tracks": stable_tracks,
            "new_stable_tracks": new_stable_tracks,
            "lost_tracks": lost_tracks
        }

    def set_person(self,track_id,person_id):
        if track_id not in self.tracks:
            return

        self.tracks[track_id]["identified"] = True
        self.tracks[track_id]["person_id"] = person_id

    def get_track(self,track_id):
        return self.tracks.get(track_id)