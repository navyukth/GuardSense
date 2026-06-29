import time

class IdentityManager:
    def __init__(self,osnet,similarity_threshold=0.65):
        self.osnet = osnet
        self.threshold = similarity_threshold

        self.people = {}
        self.next_person_id = 1

    def identify(self,embeddings):
        if len(embeddings) == 0:
            return None

        best_person = None
        best_score = -1

        ####################################################
        # Compare with every known person
        ####################################################

        for person_id, data in self.people.items():
            score = self.compare(
                embeddings,
                data["embeddings"]
            )
            print(f"Compare -> Person {person_id}: {score:.4f}")
            if score > best_score:
                best_score = score
                best_person = person_id

        ####################################################
        # No people in database
        ####################################################
        if best_person is None:
            person_id = self.create_person(
                embeddings
            )

            print(f"Created Person {person_id}")
            print("--------------------------------")

            return person_id

        ####################################################
        # Best Match
        ####################################################

        print(f"Best Score : {best_score:.4f}")

        if best_score >= self.threshold:
            print(f"Matched -> Person {best_person}")
            self.update_person(
                best_person,
                embeddings
            )

            print("--------------------------------")

            return best_person

        ####################################################
        # Candidate Zone
        ####################################################

        if best_score >= 0.60:
            print("Candidate Match - Waiting for more embeddings...")

            print("--------------------------------")

            return None

        ####################################################
        # New Person
        ####################################################
        person_id = self.create_person(
            embeddings
        )
        print(f"Created Person {person_id}")
        print("--------------------------------")
        return person_id

    def compare(self,query_embeddings,database_embeddings):
        best = -1
        for query in query_embeddings:
            for database in database_embeddings:
                score = self.osnet.similarity(
                    query,
                    database
                )
                if score > best:
                    best = score
        return best

    def create_person(self,embeddings):
        person_id = self.next_person_id

        self.next_person_id += 1

        self.people[person_id] = {
            "person_id": person_id,
            "embeddings": embeddings.copy(),
            "first_seen": time.time(),
            "last_seen": time.time()
        }
        return person_id

    def update_person(self,person_id,embeddings):
        self.people[person_id]["last_seen"] = time.time()
        self.people[person_id]["embeddings"] = embeddings.copy()

    def get_person(self,person_id):

        return self.people.get(person_id)