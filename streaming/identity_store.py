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

import cv2
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
CREATE INDEX IF NOT EXISTS idx_crops_created ON crops(created_at);

-- Alerts used to live only in the relay's memory, so every restart/deploy
-- wiped the history. alert_id is minted by the capture node when the track
-- first appears; later label updates (track matched to a name) upsert the
-- same row.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    track_id INTEGER,
    person_id INTEGER,
    label TEXT NOT NULL,
    ts REAL NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

-- Who did what in the admin UI (deletes, merges, moves). Answers "where did
-- my crops go?" after the fact.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    remote TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
"""


def _connect():
    # generous busy timeout: the retention job's VACUUM briefly locks the
    # whole DB, and ingest writes should wait it out instead of erroring
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    os.makedirs(CROPS_DIR, exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(SCHEMA)

        # quality was added after the first deploy - migrate old DBs in place
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(crops)")}
        if "quality" not in columns:
            conn.execute("ALTER TABLE crops ADD COLUMN quality REAL")

        conn.commit()
    finally:
        conn.close()


def compute_quality(image_bgr):
    """0..1 heuristic for 'is this crop worth keeping': sharpness (variance
    of the Laplacian - blurry/motion-smeared crops score low) weighted over
    size (tiny far-away crops carry little identity signal)."""

    if image_bgr is None or image_bgr.size == 0:
        return 0.0

    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    sharpness = min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 150.0)
    size = min(1.0, (height * width) / 20000.0)
    return round(0.6 * sharpness + 0.4 * size, 4)


def _quality_from_jpeg(jpeg_bytes):
    image = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    return compute_quality(image)


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


def add_crop(camera_id, track_id, jpeg_bytes, embedding, person_id=None):
    """person_id lets the capture node auto-tag a crop it already matched
    to a known person via live re-id, instead of every crop landing in the
    unassigned queue for manual review every single time."""

    filename = f"{uuid.uuid4().hex}.jpg"
    with open(os.path.join(CROPS_DIR, filename), "wb") as f:
        f.write(jpeg_bytes)

    session_key = f"{camera_id}:{track_id}"
    quality = _quality_from_jpeg(jpeg_bytes)

    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO crops (person_id, session_key, camera_id, track_id,
                                   filename, embedding, created_at, quality)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (person_id, session_key, camera_id, track_id, filename, _pack(embedding),
             time.time(), quality)
        )
        if person_id is not None:
            _recompute_person_embedding(conn, person_id)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_person_embeddings():
    """Every named person that has an embedding yet, for the capture
    node's live re-id matcher to pull down periodically."""

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, name, embedding FROM persons WHERE embedding IS NOT NULL"
        ).fetchall()
        return [
            {"id": row["id"], "name": row["name"], "embedding": _unpack(row["embedding"]).tolist()}
            for row in rows
        ]
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
    else:
        # No crops left -> nothing to base an identity on. Leaving the old
        # mean in place let an emptied person keep matching new detections
        # forever. The person (name) stays; only the embedding goes.
        conn.execute("UPDATE persons SET embedding = NULL WHERE id = ?", (person_id,))


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


# =========================================================
# Alerts (persisted) and audit log
# =========================================================

def upsert_alerts(alerts):
    """Stores/updates alerts pushed by the capture node. Entries without an
    id (e.g. from an older capture build) are skipped."""

    rows = [
        (a["id"], a["camera_id"], a.get("track_id"), a.get("person_id"),
         a["label"], a.get("ts") or time.time(), a["timestamp"])
        for a in alerts if a.get("id")
    ]
    if not rows:
        return

    conn = _connect()
    try:
        conn.executemany(
            """INSERT INTO alerts (alert_id, camera_id, track_id, person_id, label, ts, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(alert_id) DO UPDATE SET
                   label = excluded.label, person_id = excluded.person_id""",
            rows
        )
        conn.commit()
    finally:
        conn.close()


def list_alerts(limit=100, since_ts=None, until_ts=None, camera_id=None):
    where, params = [], []
    if since_ts is not None:
        where.append("ts >= ?")
        params.append(since_ts)
    if until_ts is not None:
        where.append("ts < ?")
        params.append(until_ts)
    if camera_id:
        where.append("camera_id = ?")
        params.append(camera_id)

    sql = "SELECT * FROM alerts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    conn = _connect()
    try:
        return [
            {
                "id": r["alert_id"], "camera_id": r["camera_id"], "track_id": r["track_id"],
                "person_id": r["person_id"], "label": r["label"], "ts": r["ts"],
                "timestamp": r["timestamp"],
            }
            for r in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()


def purge_old_alerts(max_age_days):
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM alerts WHERE ts < ?", (time.time() - max_age_days * 86400,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def add_audit(ts, action, detail, remote):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO audit_log (ts, action, detail, remote) VALUES (?, ?, ?, ?)",
            (ts, action, detail, remote)
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(limit=200):
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT ts, action, detail, remote FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()


def move_crops(crop_ids, to_person_id):
    """Reassigns specific crops to a different person - the fix for 'this
    person card actually has two different people mixed into it': select
    the wrong ones, move them to a (possibly new) correct person."""

    if not crop_ids:
        return

    conn = _connect()
    try:
        placeholders = ",".join("?" * len(crop_ids))
        rows = conn.execute(
            f"SELECT DISTINCT person_id FROM crops WHERE id IN ({placeholders})", crop_ids
        ).fetchall()
        source_person_ids = {r["person_id"] for r in rows if r["person_id"] is not None}

        conn.execute(
            f"UPDATE crops SET person_id = ? WHERE id IN ({placeholders})",
            [to_person_id, *crop_ids]
        )

        for person_id in source_person_ids | {to_person_id}:
            _recompute_person_embedding(conn, person_id)

        conn.commit()
    finally:
        conn.close()


def delete_crops_bulk(crop_ids):
    """Bulk crop delete (e.g. from a person's gallery, or a multi-select
    across several unassigned groups) - recomputes each affected person's
    embedding once at the end instead of once per crop."""

    if not crop_ids:
        return 0

    conn = _connect()
    try:
        placeholders = ",".join("?" * len(crop_ids))
        rows = conn.execute(
            f"SELECT DISTINCT person_id FROM crops WHERE id IN ({placeholders})", crop_ids
        ).fetchall()
        affected_person_ids = {r["person_id"] for r in rows if r["person_id"] is not None}

        _delete_crops(conn, crop_ids)

        for person_id in affected_person_ids:
            _recompute_person_embedding(conn, person_id)

        conn.commit()
        return len(crop_ids)
    finally:
        conn.close()


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


def delete_groups(session_keys):
    """Bulk version of delete_group - one admin action to clear out a pile
    of false-positive sightings (bikes, shadows, ...) instead of clicking
    through them one at a time."""

    if not session_keys:
        return 0

    conn = _connect()
    try:
        placeholders = ",".join("?" * len(session_keys))
        rows = conn.execute(
            f"SELECT id FROM crops WHERE session_key IN ({placeholders}) AND person_id IS NULL",
            session_keys
        ).fetchall()

        if rows:
            _delete_crops(conn, [r["id"] for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def delete_group(session_key):
    """Discards an unlabeled group entirely - every crop for that track,
    deleted outright, no person ever created for it."""

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id FROM crops WHERE session_key = ? AND person_id IS NULL",
            (session_key,)
        ).fetchall()

        if rows:
            _delete_crops(conn, [r["id"] for r in rows])
        conn.commit()
    finally:
        conn.close()


# =========================================================
# Retention: keep the store small without losing anything that matters
# =========================================================

def _delete_crops_chunked(conn, crop_ids, chunk=500):
    # SQLite caps bound variables per statement - a big purge can be tens
    # of thousands of ids
    for i in range(0, len(crop_ids), chunk):
        _delete_crops(conn, crop_ids[i:i + chunk])


def purge_unassigned(max_age_seconds):
    """Deletes every unlabeled crop (row, embedding and image file) older
    than max_age_seconds. Named people's crops are never touched. Rolling
    rather than a wipe-everything-every-2-days, so a sighting from an hour
    ago is still there to review."""

    cutoff = time.time() - max_age_seconds
    conn = _connect()
    try:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM crops WHERE person_id IS NULL AND created_at < ?", (cutoff,)
        ).fetchall()]
        _delete_crops_chunked(conn, ids)
        conn.commit()
        return len(ids)
    finally:
        conn.close()


def _backfill_quality(conn, rows):
    """Crops stored before the quality column existed have NULL there -
    score them from their image files so pruning has something to rank on."""

    updated = 0
    for row in rows:
        if row["quality"] is not None:
            continue
        path = os.path.join(CROPS_DIR, row["filename"])
        image = cv2.imread(path)
        quality = compute_quality(image) if image is not None else 0.0
        conn.execute("UPDATE crops SET quality = ? WHERE id = ?", (quality, row["id"]))
        updated += 1
    if updated:
        conn.commit()


def prune_person(person_id, keep, min_similarity=0.45, min_quality=0.1):
    """Shrinks one person's crops down to at most `keep`, choosing the ones
    that are most useful as identity references:

      1. drop outliers - crops whose embedding is far from the person's own
         centroid are usually mis-assigned or a different person entirely
      2. drop junk - blurry / tiny crops, as long as enough remain
      3. of what's left, greedily pick a varied set (farthest-point
         sampling over embeddings, weighted by quality) so the kept crops
         cover different poses/angles/lighting instead of 300 near-copies
         of the same second of video

    Returns how many crops were deleted."""

    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT id, filename, embedding, quality FROM crops
               WHERE person_id = ? AND embedding IS NOT NULL""",
            (person_id,)
        ).fetchall()

        if len(rows) <= keep:
            return 0

        _backfill_quality(conn, rows)
        rows = conn.execute(
            """SELECT id, embedding, quality FROM crops
               WHERE person_id = ? AND embedding IS NOT NULL""",
            (person_id,)
        ).fetchall()

        ids = np.array([r["id"] for r in rows])
        vectors = np.stack([_unpack(r["embedding"]) for r in rows]).astype(np.float32)
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
        quality = np.array([r["quality"] or 0.0 for r in rows], dtype=np.float32)

        centroid = vectors.mean(axis=0)
        centroid /= max(np.linalg.norm(centroid), 1e-8)
        similarity = vectors @ centroid

        eligible = similarity >= min_similarity
        if eligible.sum() > keep:
            good = eligible & (quality >= min_quality)
            if good.sum() >= keep:
                eligible = good

        candidates = np.where(eligible)[0]
        if len(candidates) == 0:
            return 0

        if len(candidates) > keep:
            cand_vectors = vectors[candidates]
            cand_quality = quality[candidates]

            first = int(np.argmax(cand_quality))
            selected = [first]
            min_distance = 1.0 - cand_vectors @ cand_vectors[first]

            for _ in range(keep - 1):
                score = min_distance * (0.5 + 0.5 * cand_quality)
                score[selected] = -1.0
                pick = int(np.argmax(score))
                selected.append(pick)
                min_distance = np.minimum(min_distance, 1.0 - cand_vectors @ cand_vectors[pick])

            candidates = candidates[selected]

        keep_ids = set(int(i) for i in ids[candidates])
        delete_ids = [int(i) for i in ids if int(i) not in keep_ids]

        _delete_crops_chunked(conn, delete_ids)
        _recompute_person_embedding(conn, person_id)
        conn.commit()
        return len(delete_ids)
    finally:
        conn.close()


def prune_all_persons(keep, min_similarity=0.45):
    conn = _connect()
    try:
        person_ids = [r["person_id"] for r in conn.execute(
            "SELECT person_id FROM crops WHERE person_id IS NOT NULL "
            "GROUP BY person_id HAVING COUNT(*) > ?", (keep,)
        ).fetchall()]
    finally:
        conn.close()

    deleted = 0
    for person_id in person_ids:
        deleted += prune_person(person_id, keep, min_similarity)
    return deleted


def run_retention(unassigned_max_age_seconds, person_max_crops, person_min_similarity,
                  alert_max_age_days=30):
    """One full maintenance pass; returns a summary dict. VACUUMs afterwards
    if a lot was deleted, since SQLite never shrinks its file on its own."""

    purged = purge_unassigned(unassigned_max_age_seconds)
    pruned = prune_all_persons(person_max_crops, person_min_similarity)
    alerts_purged = purge_old_alerts(alert_max_age_days)

    if purged + pruned > 500:
        conn = _connect()
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    return {"unassigned_purged": purged, "person_pruned": pruned, "alerts_purged": alerts_purged}


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
        return len(rows)
    finally:
        conn.close()
