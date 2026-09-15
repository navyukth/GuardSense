"""
Pi5-side relay: terminates WebRTC/STUN/TURN and serves the browser page,
but does NO capture or inference itself — no torch/ultralytics/opencv-heavy
deps needed here at all. Frames, alerts, and status are pushed in over a
WebSocket by streaming/capture_node.py running on the laptop (or wherever
the actual GuardSensePipeline is running).

Run with:
    python -m streaming.relay_server
"""

import asyncio
import os
import struct
import time
from collections import deque

import cv2
import numpy as np
from aiohttp import web, WSMsgType
from dotenv import load_dotenv

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    RTCConfiguration,
    RTCIceServer,
)

from av import VideoFrame

from streaming.webui import Auth, render_html, render_settings_html
from streaming.people_ui import render_people_html
from streaming.logs_ui import render_logs_html
from streaming import identity_store


# =========================================================
# Environment
# =========================================================

load_dotenv()

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
TURN_USERNAME = os.environ["TURN_USERNAME"]
TURN_CREDENTIAL = os.environ["TURN_CREDENTIAL"]
TURN_HOST = os.environ["TURN_HOST"]

# Shared secret the capture node must present to push frames in — this
# endpoint has no other auth, and it's reachable from the LAN (or the
# internet, if this box is publicly exposed), so it can't be left open.
INGEST_TOKEN = os.environ["RELAY_INGEST_TOKEN"]

CAMERA_IDS = [c.strip() for c in os.environ["CAMERA_IDS"].split(",") if c.strip()]

PORT = int(os.environ.get("RELAY_PORT", "8080"))


# =========================================================
# STUN / TURN ICE Configuration — same coturn the capture node's
# browser-facing offer will negotiate through
# =========================================================

ICE_SERVERS = [
    {"urls": f"stun:{TURN_HOST}:3478"},
    {
        "urls": f"turn:{TURN_HOST}:3478",
        "username": TURN_USERNAME,
        "credential": TURN_CREDENTIAL,
    },
    {
        "urls": f"turn:{TURN_HOST}:3478?transport=tcp",
        "username": TURN_USERNAME,
        "credential": TURN_CREDENTIAL,
    },
]

ICE_CONFIG = RTCConfiguration(
    iceServers=[RTCIceServer(**server) for server in ICE_SERVERS]
)


# =========================================================
# State pushed in from the capture node over /ws/ingest
# =========================================================

server_started_at = time.time()

pcs = set()

auth = Auth(ADMIN_PASSWORD, exempt_paths=("/ws/ingest", "/api/internal/people-embeddings"))


FRAME_STALE_SECONDS = 5.0


class FrameStore:
    """
    Latest annotated frame per camera, as received from the capture node.
    Mirrors the shape of GuardSensePipeline.get_latest_frame() so
    RelayVideoTrack can stay nearly identical to GuardSenseVideoTrack.

    Tracks a per-camera timestamp too - without it, RelayVideoTrack would
    happily keep re-serving the last frame it ever received forever if the
    capture node dies, which looks exactly like a live (but frozen) feed
    instead of an obviously dead one.
    """

    def __init__(self):
        self.frames = {}
        self.frame_updated_at = {}
        self.lock = asyncio.Lock()
        self.last_ingest_at = None

    async def set_frame(self, camera_id, frame):
        async with self.lock:
            self.frames[camera_id] = frame
            self.frame_updated_at[camera_id] = time.time()
            self.last_ingest_at = time.time()

    async def get_frame(self, camera_id):
        async with self.lock:
            frame = self.frames.get(camera_id)
            updated_at = self.frame_updated_at.get(camera_id)

        if frame is None or updated_at is None:
            return None
        if time.time() - updated_at > FRAME_STALE_SECONDS:
            return None
        return frame


MAX_LOG_LINES = 500


class RelayState:
    """Latest alerts/status/logs JSON pushed in from the capture node."""

    def __init__(self):
        self.alerts = []
        self.capture_status = {}
        self.logs = deque(maxlen=MAX_LOG_LINES)
        self.lock = asyncio.Lock()

    async def set_alerts(self, alerts):
        async with self.lock:
            self.alerts = alerts

    async def set_status(self, status):
        async with self.lock:
            self.capture_status = status

    async def add_logs(self, entries):
        async with self.lock:
            self.logs.extend(entries)

    async def get_logs(self):
        async with self.lock:
            return list(self.logs)


frame_store = FrameStore()
relay_state = RelayState()


# =========================================================
# WebRTC Video Track — pulls from FrameStore instead of a
# locally-running GuardSensePipeline
# =========================================================

def _placeholder_frame(camera_id):
    # Built directly in RGB (not BGR) since this bypasses the normal
    # cvtColor(BGR2RGB) conversion applied to real camera frames - using
    # only gray/white keeps that irrelevant either way.
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(
        image, "No live feed", (140, 220),
        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (220, 220, 220), 2, cv2.LINE_AA
    )
    cv2.putText(
        image, f"({camera_id} - capture node disconnected)", (60, 260),
        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (140, 140, 140), 1, cv2.LINE_AA
    )
    return image


class RelayVideoTrack(VideoStreamTrack):

    def __init__(self, camera_id):
        super().__init__()
        self.camera_id = camera_id

    async def recv(self):

        pts, time_base = await self.next_timestamp()

        frame = await frame_store.get_frame(self.camera_id)

        if frame is None:
            frame = _placeholder_frame(self.camera_id)
        else:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        video_frame = VideoFrame.from_ndarray(frame, format="rgb24")
        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame


# =========================================================
# Ingest — capture node connects here and pushes frames +
# alerts/status over one persistent WebSocket
#
# Wire format:
#   binary frame: [1 byte camera_id length][camera_id utf8][JPEG bytes]
#   text frame:   JSON {"type": "alerts"|"status", "data": ...}
# =========================================================

MSG_TYPE_FRAME = 0
MSG_TYPE_CROP = 1


def _parse_frame_message(data):
    id_len = data[1]
    camera_id = data[2:2 + id_len].decode("utf-8")
    jpg_bytes = data[2 + id_len:]
    return camera_id, jpg_bytes


def _parse_crop_message(data):
    offset = 1
    id_len = data[offset]
    offset += 1
    camera_id = data[offset:offset + id_len].decode("utf-8")
    offset += id_len

    track_id = struct.unpack(">I", data[offset:offset + 4])[0]
    offset += 4

    emb_len = struct.unpack(">H", data[offset:offset + 2])[0]
    offset += 2

    embedding = np.frombuffer(data[offset:offset + emb_len * 4], dtype=np.float32)
    offset += emb_len * 4

    # signed - the capture node sends -1 when its live re-id matcher found
    # no known person above threshold, otherwise the matched person's id
    person_id = struct.unpack(">i", data[offset:offset + 4])[0]
    offset += 4

    jpg_bytes = data[offset:]

    return camera_id, track_id, embedding, person_id, jpg_bytes


async def ws_ingest(request):

    token = request.query.get("token")

    if token != INGEST_TOKEN:
        raise web.HTTPUnauthorized(text="bad ingest token")

    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024)
    await ws.prepare(request)

    print("Capture node connected to /ws/ingest")

    async for msg in ws:

        if msg.type == WSMsgType.BINARY:

            data = msg.data
            msg_type = data[0]

            if msg_type == MSG_TYPE_FRAME:

                camera_id, jpg_bytes = _parse_frame_message(data)

                frame = cv2.imdecode(
                    np.frombuffer(jpg_bytes, dtype=np.uint8),
                    cv2.IMREAD_COLOR
                )

                if frame is not None:
                    await frame_store.set_frame(camera_id, frame)

            elif msg_type == MSG_TYPE_CROP:

                camera_id, track_id, embedding, person_id, jpg_bytes = _parse_crop_message(data)

                await asyncio.to_thread(
                    identity_store.add_crop, camera_id, track_id, jpg_bytes, embedding,
                    person_id if person_id >= 0 else None
                )

        elif msg.type == WSMsgType.TEXT:

            import json
            payload = json.loads(msg.data)

            if payload.get("type") == "alerts":
                await relay_state.set_alerts(payload["data"])
            elif payload.get("type") == "status":
                await relay_state.set_status(payload["data"])
            elif payload.get("type") == "logs":
                await relay_state.add_logs(payload["data"])

        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
            break

    print("Capture node disconnected from /ws/ingest")

    return ws


# =========================================================
# HTTP Routes
# =========================================================

async def index(request):
    return web.Response(
        text=render_html(ICE_SERVERS),
        content_type="text/html"
    )


async def cameras(request):
    return web.json_response({"camera_ids": CAMERA_IDS})


async def logs_page(request):
    return web.Response(text=render_logs_html(), content_type="text/html")


async def settings_page(request):
    config = {
        "ADMIN_PASSWORD": ADMIN_PASSWORD,
        "TURN_HOST": TURN_HOST,
        "TURN_USERNAME": TURN_USERNAME,
        "TURN_CREDENTIAL": TURN_CREDENTIAL,
        "RELAY_INGEST_TOKEN": INGEST_TOKEN,
        "CAMERA_IDS": ", ".join(CAMERA_IDS),
    }
    return web.Response(text=render_settings_html(config), content_type="text/html")


async def people_page(request):
    return web.Response(text=render_people_html(), content_type="text/html")


async def crop_image(request):
    filename = request.match_info["filename"]
    # match_info comes straight off the URL path; refuse anything that
    # could climb out of the crops directory before it ever reaches disk
    if "/" in filename or "\\" in filename or ".." in filename:
        raise web.HTTPBadRequest(text="invalid filename")

    path = os.path.join(identity_store.CROPS_DIR, filename)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()

    return web.FileResponse(path)


async def api_internal_people_embeddings(request):
    """Token-authenticated (not cookie-authenticated) - this is what the
    capture node's live re-id matcher polls, same trust boundary as
    /ws/ingest since it's the same process on the other end."""

    token = request.query.get("token")
    if token != INGEST_TOKEN:
        raise web.HTTPUnauthorized(text="bad ingest token")

    people = await asyncio.to_thread(identity_store.list_person_embeddings)
    return web.json_response({"people": people})


async def api_people(request):
    people = await asyncio.to_thread(identity_store.list_persons)
    return web.json_response({"people": people})


async def api_unassigned(request):
    groups = await asyncio.to_thread(identity_store.list_unassigned_groups)
    return web.json_response({"groups": groups})


async def api_group_crops(request):
    session_key = request.match_info["session_key"]
    crops = await asyncio.to_thread(identity_store.get_group_crops, session_key)
    return web.json_response({"crops": crops})


async def api_person_crops(request):
    person_id = int(request.match_info["person_id"])
    crops = await asyncio.to_thread(identity_store.get_person_crops, person_id)
    return web.json_response({"crops": crops})


async def api_assign(request):
    """Assigns an unlabeled group to a person - creating one if `name` is
    given, or merging straight into `person_id` if it already exists."""

    body = await request.json()
    session_key = body["session_key"]
    exclude_crop_ids = body.get("exclude_crop_ids", [])

    person_id = body.get("person_id")
    name = body.get("name")

    if person_id is None:
        if not name or not name.strip():
            raise web.HTTPBadRequest(text="name required for a new person")
        person_id = await asyncio.to_thread(identity_store.create_person, name.strip())

    await asyncio.to_thread(
        identity_store.assign_group_to_person, session_key, person_id, exclude_crop_ids
    )
    return web.json_response({"person_id": person_id})


async def api_merge(request):
    body = await request.json()
    from_id = int(body["from_id"])
    into_id = int(body["into_id"])
    exclude_crop_ids = body.get("exclude_crop_ids", [])

    await asyncio.to_thread(identity_store.merge_persons, from_id, into_id, exclude_crop_ids)
    return web.json_response({"person_id": into_id})


async def api_delete_group(request):
    session_key = request.match_info["session_key"]
    await asyncio.to_thread(identity_store.delete_group, session_key)
    return web.json_response({"ok": True})


async def api_delete_person(request):
    person_id = int(request.match_info["person_id"])
    await asyncio.to_thread(identity_store.delete_person, person_id)
    return web.json_response({"ok": True})


async def api_delete_crop(request):
    crop_id = int(request.match_info["crop_id"])
    await asyncio.to_thread(identity_store.delete_crop, crop_id)
    return web.json_response({"ok": True})


async def alerts(request):
    return web.json_response({"alerts": relay_state.alerts})


async def logs(request):
    return web.json_response({"logs": await relay_state.get_logs()})


async def status(request):

    uptime_seconds = int(time.time() - server_started_at)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    capture_connected = (
        frame_store.last_ingest_at is not None
        and (time.time() - frame_store.last_ingest_at) < 10
    )

    merged = {
        "device": relay_state.capture_status.get("device", "unknown"),
        "camera_ids": CAMERA_IDS,
        "uptime_human": f"{hours}h {minutes}m {seconds}s",
        "active_connections": len(pcs),
        "capture_node_connected": capture_connected,
        "capture_loop_count": relay_state.capture_status.get("loop_count"),
    }

    return web.json_response(merged)


async def offer(request):

    params = await request.json()

    camera_id = params.get("camera_id", CAMERA_IDS[0])

    if camera_id not in CAMERA_IDS:
        camera_id = CAMERA_IDS[0]

    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection(configuration=ICE_CONFIG)
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    track = RelayVideoTrack(camera_id)
    pc.addTrack(track)

    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
        "camera_id": camera_id
    })


async def shutdown(app):

    await asyncio.gather(
        *[pc.close() for pc in pcs],
        return_exceptions=True
    )
    pcs.clear()


# =========================================================
# AIOHTTP Application
# =========================================================

app = web.Application(middlewares=[auth.middleware])

app.router.add_get("/", index)
app.router.add_get("/login", auth.login_page)
app.router.add_post("/login", auth.login_submit)
app.router.add_get("/logout", auth.logout)
app.router.add_get("/settings", settings_page)
app.router.add_get("/people", people_page)
app.router.add_get("/logs", logs_page)
app.router.add_get("/crop-image/{filename}", crop_image)
app.router.add_get("/api/cameras", cameras)
app.router.add_get("/api/logs", logs)
app.router.add_get("/api/internal/people-embeddings", api_internal_people_embeddings)
app.router.add_get("/api/people", api_people)
app.router.add_get("/api/people/unassigned", api_unassigned)
app.router.add_get("/api/people/group/{session_key}/crops", api_group_crops)
app.router.add_get("/api/people/{person_id}/crops", api_person_crops)
app.router.add_post("/api/people/assign", api_assign)
app.router.add_post("/api/people/merge", api_merge)
app.router.add_delete("/api/people/group/{session_key}", api_delete_group)
app.router.add_delete("/api/people/{person_id}", api_delete_person)
app.router.add_delete("/api/crops/{crop_id}", api_delete_crop)
app.router.add_get("/api/alerts", alerts)
app.router.add_get("/api/status", status)
app.router.add_post("/offer", offer)
app.router.add_get("/ws/ingest", ws_ingest)

app.on_shutdown.append(shutdown)


if __name__ == "__main__":

    print("Starting GuardSense relay (Pi5 mode — no local capture/inference)")

    identity_store.init_db()

    web.run_app(app, host="0.0.0.0", port=PORT)
