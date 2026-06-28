import cv2
import numpy as np

class ZoneManager:

    def __init__(self):
        self.zones = []

    def add_zone(self, name, points):
        self.zones.append({
            "name": name,
            "points": np.array(points, dtype=np.int32)
        })

    def check(self, people):
        results = []
        for person in people:
            x1, y1, x2, y2 = person["bbox"]
            # Bottom-center of the bounding box
            point = (
                (x1 + x2) // 2,
                y2
            )
            for zone in self.zones:
                inside = cv2.pointPolygonTest(
                    zone["points"],
                    point,
                    False
                ) >= 0
                results.append({
                    "person_id": person["id"],
                    "zone": zone["name"],
                    "inside": inside
                })
        return results