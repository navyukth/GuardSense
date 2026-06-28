import cv2


class Renderer:
    @staticmethod
    def draw(frame, people):
        for person in people:
            x1, y1, x2, y2 = person["bbox"]
            track = person.get("track")
            if track is not None and track["identified"]:
                if "label" in person:
                    label = person["label"]
                elif track is not None and track["identified"]:
                    label = f"Person {track['person_id']}"
                else:
                    label = f"Track {person['id']}"
                color = (0, 255, 0)      # Green
            else:
                label = f"Track {person['id']}"
                color = (0, 165, 255)    # Orange
            cv2.rectangle(
                frame,
                (x1, y1),
                (x2, y2),
                color,
                2
            )
            cv2.putText(
                frame,
                label,
                (x1, y1 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2
            )
        return frame