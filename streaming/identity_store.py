"""
SQLite-backed store for named people and their re-id crops, used by the
/people review page. Lives on the relay (Pi5) side since that's what
serves the web UI - the capture node just ships crop images + OSNet
embeddings in over the ingest websocket, and everything else (grouping
unlabeled tracks, naming, merging, deleting) happens here.

All DB calls are blocking sqlite3 - callers from aiohttp handlers should
run them via asyncio.to_thread.
"""

import os
import pickle
import sqlite3
import time
import uuid

import numpy as np

DB_PATH = os.environ.get("IDENTITY_DB_PATH", "identities.db")
CROPS_DIR = os.environ.get("RELAY_CROPS_DIR", "crops_relay")

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    embedding BLOB,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS crops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER REFERENCES persons(id) ON DELETE CASCADE,
    session_key TEXT NOT NULL,
    camera_id TEXT NOT NULL,
    track_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    embedding BLOB,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_crops_person ON crops(person_id);
CREATE INDEX IF NOT EXISTS idx_crops_session ON crops(session_key);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(CROPS_DIR, exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _pack(embedding):
    return pickle.dumps(np.asarray(embedding, dtype=np.float32))


def _unpack(blob):
    if blob is None:
        return None
    return pickle.loads(blob)


def _crop_url(filename):
    return f"/crop-image/{filename}"


def _row_to_crop(row):
    return {
        "id": row["id"],
        "person_id": row["person_id"],
        "session_key": row["session_key"],
        "camera_id": row["camera_id"],
        "track_id": row["track_id"],
        "url": _crop_url(row["filename"]),
        "created_at": row["created_at"],
    }


def add_crop(camera_id, track_id, jpeg_bytes, embedding):
    filename = f"{uuid.uuid4().hex}.jpg"
    with open(os.path.join(CROPS_DIR, filename), "wb") as f:
        f.write(jpeg_bytes)

    session_key = f"{camera_id}:{track_id}"

    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO crops (person_id, session_key, camera_id, track_id,
                                   filename, embedding, created_at)
               VALUES (NULL, ?, ?, ?, ?, ?, ?)""",
            (session_key, camera_id, track_id, filename, _pack(embedding), time.time())
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_unassigned_groups():
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT session_key, camera_id, track_id,
                      COUNT(*) as crop_count,
                      MIN(created_at) as first_at, MAX(created_at) as last_at
               FROM crops WHERE person_id IS NULL
               GROUP BY session_key
               ORDER BY last_at DESC"""
        ).fetchall()

        groups = []
        for row in rows:
            first_crop = conn.execute(
                """SELECT * FROM crops WHERE session_key = ? AND person_id IS NULL
                   ORDER BY created_at ASC LIMIT 1""",
                (row["session_key"],)
            ).fetchone()
            last_crop = conn.execute(
                """SELECT * FROM crops WHERE session_key = ? AND person_id IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (row["session_key"],)
            ).fetchone()

            groups.append({
                "session_key": row["session_key"],
                "camera_id": row["camera_id"],
                "track_id": row["track_id"],
                "crop_count": row["crop_count"],
                "oldest": _row_to_crop(first_crop),
                "newest": _row_to_crop(last_crop),
            })
        return groups
    finally:
        conn.close()


def get_group_crops(session_key):
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT * FROM crops WHERE session_key = ? AND person_id IS NULL
               ORDER BY created_at ASC""",
            (session_key,)
        ).fetchall()
        return [_row_to_crop(r) for r in rows]
    finally:
        conn.close()


def list_persons():
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT p.id, p.name, COUNT(c.id) as crop_count
               FROM persons p LEFT JOIN crops c ON c.person_id = p.id
               GROUP BY p.id ORDER BY p.name COLLATE NOCASE ASC"""
        ).fetchall()

        people = []
        for row in rows:
            oldest = conn.execute(
                "SELECT * FROM crops WHERE person_id = ? ORDER BY created_at ASC LIMIT 1",
                (row["id"],)
            ).fetchone()
            newest = conn.execute(
                "SELECT * FROM crops WHERE person_id = ? ORDER BY created_at DESC LIMIT 1",
                (row["id"],)
            ).fetchone()

            people.append({
                "id": row["id"],
                "name": row["name"],
                "crop_count": row["crop_count"],
                "oldest": _row_to_crop(oldest) if oldest else None,
                "newest": _row_to_crop(newest) if newest else None,
            })
        return people
    finally:
        conn.close()


def get_person_crops(person_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM crops WHERE person_id = ? ORDER BY created_at ASC",
            (person_id,)
        ).fetchall()
        return [_row_to_crop(r) for r in rows]
    finally:
        conn.close()


def get_person(person_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM persons WHERE id = ?", (person_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _recompute_person_embedding(conn, person_id):
    rows = conn.execute(
        "SELECT embedding FROM crops WHERE person_id = ? AND embedding IS NOT NULL",
        (person_id,)
    ).fetchall()

    vectors = [_unpack(r["embedding"]) for r in rows if r["embedding"] is not None]

    if vectors:
        mean_vector = np.mean(np.stack(vectors), axis=0)
        conn.execute(
            "UPDATE persons SET embedding = ? WHERE id = ?",
            (_pack(mean_vector), person_id)
        )


def create_person(name):
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO persons (name, embedding, created_at) VALUES (?, NULL, ?)",
            (name, time.time())
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def assign_group_to_person(session_key, person_id, exclude_crop_ids=None):
    """Assigns every remaining (non-excluded) crop of an unlabeled group to
    an existing or newly-created person. Excluded crops are deleted outright
    - they're outliers the admin chose to drop, not just unlinked."""

    exclude_crop_ids = exclude_crop_ids or []

    conn = _connect()
    try:
        if exclude_crop_ids:
            _delete_crops(conn, exclude_crop_ids)

        conn.execute(
            "UPDATE crops SET person_id = ? WHERE session_key = ? AND person_id IS NULL",
            (person_id, session_key)
        )
        _recompute_person_embedding(conn, person_id)
        conn.commit()
    finally:
        conn.close()


def merge_persons(from_id, into_id, exclude_crop_ids=None):
    """Moves every remaining crop from `from_id` into `into_id`, deletes any
    crops the admin excluded as outliers, then deletes the now-empty
    `from_id` person. Embeddings on `into_id` are recomputed from the
    combined crop set."""

    if from_id == into_id:
        raise ValueError("cannot merge a person into themselves")

    exclude_crop_ids = exclude_crop_ids or []

    conn = _connect()
    try:
        if exclude_crop_ids:
            _delete_crops(conn, exclude_crop_ids)

        conn.execute(
            "UPDATE crops SET person_id = ? WHERE person_id = ?",
            (into_id, from_id)
        )
        conn.execute("DELETE FROM persons WHERE id = ?", (from_id,))
        _recompute_person_embedding(conn, into_id)
        conn.commit()
    finally:
        conn.close()


def _delete_crops(conn, crop_ids):
    placeholders = ",".join("?" * len(crop_ids))
    rows = conn.execute(
        f"SELECT filename FROM crops WHERE id IN ({placeholders})", crop_ids
    ).fetchall()

    for row in rows:
        path = os.path.join(CROPS_DIR, row["filename"])
        try:
            os.remove(path)
        except OSError:
            pass

    conn.execute(f"DELETE FROM crops WHERE id IN ({placeholders})", crop_ids)


def delete_crop(crop_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT person_id FROM crops WHERE id = ?", (crop_id,)).fetchone()
        _delete_crops(conn, [crop_id])
        if row and row["person_id"] is not None:
            _recompute_person_embedding(conn, row["person_id"])
        conn.commit()
    finally:
        conn.close()


def delete_person(person_id):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT filename FROM crops WHERE person_id = ?", (person_id,)
        ).fetchall()

        for row in rows:
            path = os.path.join(CROPS_DIR, row["filename"])
            try:
                os.remove(path)
            except OSError:
                pass

        conn.execute("DELETE FROM crops WHERE person_id = ?", (person_id,))
        conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        conn.commit()
    finally:
        conn.close()
