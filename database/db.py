import os
import sqlite3
import threading
import uuid
from datetime import datetime

import cv2
import numpy as np


def _to_readable(ts):
    """
    Converts a raw epoch float (time.time()) into a human-readable
    'YYYY-MM-DD HH:MM:SS' string. ISO-style format sorts correctly
    as plain text too, so ORDER BY still works normally in SQL.
    """
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class GuardSenseDB:

    def __init__(self, path="guardsense.db", crops_dir="crops"):

        self.path = path
        self.crops_dir = crops_dir
        self.lock = threading.Lock()

        os.makedirs(self.crops_dir, exist_ok=True)

        self._init_schema()

    def _connect(self):
        return sqlite3.connect(self.path, check_same_thread=False)

    def _init_schema(self):
        with self.lock:
            conn = self._connect()

            conn.execute("""
                CREATE TABLE IF NOT EXISTS persons (
                    person_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT,
                    first_seen TEXT,
                    last_seen TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id INTEGER NOT NULL,
                    track_id INTEGER,
                    camera_id TEXT,
                    vector BLOB NOT NULL,
                    image_path TEXT,
                    timestamp TEXT,
                    FOREIGN KEY(person_id) REFERENCES persons(person_id)
                )
            """)

            conn.commit()
            conn.close()

    def create_person(self, ts, label=None):

        readable_ts = _to_readable(ts)

        with self.lock:
            conn = self._connect()

            cur = conn.execute(
                "INSERT INTO persons (label, first_seen, last_seen) VALUES (?, ?, ?)",
                (label, readable_ts, readable_ts)
            )

            conn.commit()

            person_id = cur.lastrowid

            conn.close()

            return person_id

    def _save_crop(self, crop: np.ndarray, person_id: int) -> str:
        """
        Saves the crop image to disk under crops/<person_id>/<uuid>.jpg
        Returns the relative file path.
        """

        person_dir = os.path.join(self.crops_dir, str(person_id))
        os.makedirs(person_dir, exist_ok=True)

        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(person_dir, filename)

        cv2.imwrite(filepath, crop)

        return filepath

    def add_embedding(self, person_id, track_id, camera_id, vector, crop, ts):

        readable_ts = _to_readable(ts)

        image_path = self._save_crop(crop, person_id)

        with self.lock:
            conn = self._connect()

            conn.execute(
                "INSERT INTO embeddings "
                "(person_id, track_id, camera_id, vector, image_path, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    person_id,
                    track_id,
                    camera_id,
                    vector.astype(np.float32).tobytes(),
                    image_path,
                    readable_ts
                )
            )

            conn.execute(
                "UPDATE persons SET last_seen = ? WHERE person_id = ?",
                (readable_ts, person_id)
            )

            conn.commit()
            conn.close()

        return image_path

    def get_all_embeddings(self):
        """
        Returns list of (person_id, vector) for the entire stored gallery.
        Used to warm the in-memory matcher on startup.
        """
        with self.lock:
            conn = self._connect()

            rows = conn.execute(
                "SELECT person_id, vector FROM embeddings"
            ).fetchall()

            conn.close()

        result = []

        for person_id, blob in rows:
            vector = np.frombuffer(blob, dtype=np.float32)
            result.append((person_id, vector))

        return result

    def get_unnamed_persons(self):
        """
        Returns persons with no label yet, along with one representative
        image_path each (their most recent sighting) — for a frontend
        'who is this?' labeling screen.
        """
        with self.lock:
            conn = self._connect()

            rows = conn.execute("""
                SELECT p.person_id, p.first_seen, p.last_seen,
                       (SELECT image_path FROM embeddings e
                        WHERE e.person_id = p.person_id
                        ORDER BY e.timestamp DESC LIMIT 1) AS image_path
                FROM persons p
                WHERE p.label IS NULL
                ORDER BY p.last_seen DESC
            """).fetchall()

            conn.close()

        return [
            {
                "person_id": row[0],
                "first_seen": row[1],
                "last_seen": row[2],
                "image_path": row[3]
            }
            for row in rows
        ]

    def set_person_label(self, person_id, label):
        """
        Called when a user names a person via the frontend.
        """
        with self.lock:
            conn = self._connect()

            conn.execute(
                "UPDATE persons SET label = ? WHERE person_id = ?",
                (label, person_id)
            )

            conn.commit()
            conn.close()

    def get_named_persons(self):
        """
        Returns persons who already have a label, each with one representative
        image_path (most recent sighting) — used for the frontend's "merge
        into who?" picker screen.
        """
        with self.lock:
            conn = self._connect()

            rows = conn.execute("""
                SELECT p.person_id, p.label, p.first_seen, p.last_seen,
                       (SELECT image_path FROM embeddings e
                        WHERE e.person_id = p.person_id
                        ORDER BY e.timestamp DESC LIMIT 1) AS image_path
                FROM persons p
                WHERE p.label IS NOT NULL
                ORDER BY p.label ASC
            """).fetchall()

            conn.close()

        return [
            {
                "person_id": row[0],
                "label": row[1],
                "first_seen": row[2],
                "last_seen": row[3],
                "image_path": row[4]
            }
            for row in rows
        ]

    def get_person_label(self, person_id):
        with self.lock:
            conn = self._connect()

            row = conn.execute(
                "SELECT label FROM persons WHERE person_id = ?",
                (person_id,)
            ).fetchone()

            conn.close()

        return row[0] if row else None

    def merge_persons(self, source_person_id, target_person_id):
        """
        Called when the frontend says "these two are actually the same person."
        Every embedding + crop belonging to source_person_id is reassigned to
        target_person_id, the crop files are physically moved into the
        target's folder, and the now-empty source person row is deleted.

        Returns the number of embedding rows that were moved.
        """

        if source_person_id == target_person_id:
            return 0

        with self.lock:
            conn = self._connect()

            rows = conn.execute(
                "SELECT id, image_path FROM embeddings WHERE person_id = ?",
                (source_person_id,)
            ).fetchall()

            target_dir = os.path.join(self.crops_dir, str(target_person_id))
            os.makedirs(target_dir, exist_ok=True)

            for embedding_id, old_path in rows:

                new_path = old_path

                if old_path and os.path.exists(old_path):

                    filename = os.path.basename(old_path)
                    new_path = os.path.join(target_dir, filename)

                    os.rename(old_path, new_path)

                conn.execute(
                    "UPDATE embeddings SET person_id = ?, image_path = ? WHERE id = ?",
                    (target_person_id, new_path, embedding_id)
                )

            # Merge first_seen / last_seen onto the target
            conn.execute("""
                UPDATE persons
                SET first_seen = (
                        SELECT MIN(first_seen) FROM persons
                        WHERE person_id IN (?, ?)
                    ),
                    last_seen = (
                        SELECT MAX(last_seen) FROM persons
                        WHERE person_id IN (?, ?)
                    )
                WHERE person_id = ?
            """, (
                source_person_id, target_person_id,
                source_person_id, target_person_id,
                target_person_id
            ))

            # Retire the duplicate person row
            conn.execute(
                "DELETE FROM persons WHERE person_id = ?",
                (source_person_id,)
            )

            conn.commit()
            conn.close()

        # Clean up the now-empty source crop folder, if nothing's left in it
        source_dir = os.path.join(self.crops_dir, str(source_person_id))
        if os.path.isdir(source_dir) and not os.listdir(source_dir):
            os.rmdir(source_dir)

        return len(rows)