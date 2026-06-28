import time

from modules.osnet import OSNet

class PersonDatabase:
    def __init__(
        self,
        similarity_threshold=0.75

    ):
        
        self.osnet = OSNet()
        self.people = {}
        self.track_map = {}
        self.next_person_id = 1
        self.threshold = similarity_threshold

    def update(
        self,
        frame,
        tracked_people
    ):

        results = []

        for person in tracked_people:
            track_id = person["id"]

            if track_id in self.track_map:
                person["person_id"] = self.track_map[track_id]
                person["known"] = True
                results.append(person)
                continue

            embedding = self.osnet.extract(
                frame,
                person["bbox"]
            )

            if embedding is None:
                continue

            matched = None
            best_score = 0

            for person_id, data in self.people.items():
                score = self.osnet.similarity(
                    embedding,
                    data["embedding"]
                )

                if score > best_score:
                    best_score = score
                    matched = person_id

            if matched is not None and best_score >= self.threshold:
                person_id = matched

            else:

                person_id = self.next_person_id
                self.next_person_id += 1
                self.people[person_id] = {
                    "embedding": embedding,
                    "created": time.time()
                }

            self.track_map[track_id] = person_id
            person["person_id"] = person_id
            person["known"] = True
            results.append(person)

        return results