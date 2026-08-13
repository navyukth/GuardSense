import asyncio
import os

import cv2
import time
from aiohttp import web
from dotenv import load_dotenv

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    RTCConfiguration,
    RTCIceServer,
)

from av import VideoFrame

from streaming.guardsense_pipeline import GuardSensePipeline


# =========================================================
# Environment
# =========================================================

load_dotenv()

TURN_USERNAME = os.environ["TURN_USERNAME"]
TURN_CREDENTIAL = os.environ["TURN_CREDENTIAL"]


# =========================================================
# GuardSense Pipeline
# =========================================================

pipeline = GuardSensePipeline()

pcs = set()

# One camera at a time — keeps exactly 1 concurrent TURN relay allocation
# (well under any free-tier limit) and avoids the extra CPU/network cost
# of a second simultaneous stream.
ALL_CAMERA_IDS = pipeline.get_camera_ids()


# =========================================================
# STUN / TURN ICE Configuration (Metered.ca)
# =========================================================

ICE_CONFIG = RTCConfiguration(
    iceServers=[
        RTCIceServer(urls="stun:stun.relay.metered.ca:80"),
        RTCIceServer(
            urls="turn:global.relay.metered.ca:80",
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        ),
        RTCIceServer(
            urls="turn:global.relay.metered.ca:80?transport=tcp",
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        ),
        RTCIceServer(
            urls="turn:global.relay.metered.ca:443",
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        ),
        RTCIceServer(
            urls="turns:global.relay.metered.ca:443?transport=tcp",
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        ),
    ]
)


# =========================================================
# WebRTC Video Track
# =========================================================

class GuardSenseVideoTrack(VideoStreamTrack):

    def __init__(self, pipeline, camera_id):
        super().__init__()

        self.pipeline = pipeline
        self.camera_id = camera_id

    async def recv(self):

        # WebRTC timestamp
        pts, time_base = await self.next_timestamp()

        frame = None

        while frame is None:

            frame = self.pipeline.get_latest_frame(self.camera_id)

            if frame is None:
                await asyncio.sleep(0.01)

        # -------------------------------------------------
        # OpenCV BGR -> RGB
        # -------------------------------------------------

        frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        # -------------------------------------------------
        # OpenCV -> PyAV
        # -------------------------------------------------

        video_frame = VideoFrame.from_ndarray(
            frame,
            format="rgb24"
        )

        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame


# =========================================================
# HTML (template — TURN credentials injected at request time)
# =========================================================

HTML_TEMPLATE = """
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<title>GuardSense Live</title>

<style>

body {
    margin: 0;
    background: #111;
    color: white;
    font-family: Arial, sans-serif;
    text-align: center;
}

h1 {
    margin: 20px;
}

#status {
    margin: 10px;
    font-size: 16px;
}

#controls {
    margin: 10px;
}

button {
    background: #333;
    color: white;
    border: 1px solid #555;
    padding: 8px 16px;
    margin: 0 4px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
}

button.active {
    background: #2a7;
    border-color: #2a7;
}

#grid {
    display: flex;
    justify-content: center;
    padding: 8px;
}

.camera-tile {
    position: relative;
    background: black;
    border-radius: 6px;
    overflow: hidden;
    width: 95%;
    max-width: 1280px;
}

.camera-tile video {
    width: 100%;
    display: block;
    background: black;
}

.camera-label {
    position: absolute;
    top: 8px;
    left: 8px;
    background: rgba(0, 0, 0, 0.6);
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 14px;
    font-weight: bold;
}

</style>

</head>

<body>

<h1>GuardSense Live</h1>

<div id="status">
    Starting WebRTC...
</div>

<div id="controls"></div>

<div id="grid"></div>


<script>

let currentPc = null;
let activeCameraId = null;

async function waitForIceGatheringComplete(pc, timeoutMs = 3000) {

    if (pc.iceGatheringState === "complete") {
        return;
    }

    const gatheringComplete = new Promise((resolve) => {

        const checkState = () => {

            if (pc.iceGatheringState === "complete") {
                pc.removeEventListener("icegatheringstatechange", checkState);
                resolve();
            }
        };

        pc.addEventListener("icegatheringstatechange", checkState);
    });

    const timeout = new Promise((resolve) => {
        setTimeout(() => {
            console.log(
                "ICE gathering timeout hit (" + timeoutMs + "ms) — " +
                "proceeding with whatever candidates are gathered so far"
            );
            resolve();
        }, timeoutMs);
    });

    // Whichever finishes first — full gathering, or the timeout —
    // we proceed. Waiting for 100% completion (every possible TURN
    // candidate, including retries) is what was causing the 50-60s delay.
    await Promise.race([gatheringComplete, timeout]);
}


async function connect(cameraId) {

    console.log("======================================");
    console.log("Switching to camera:", cameraId);
    console.log("======================================");

    const status =
        document.getElementById("status");

    const grid =
        document.getElementById("grid");

    activeCameraId = cameraId;

    // Mark the active button
    document.querySelectorAll("#controls button").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.cameraId === cameraId);
    });

    // Tear down the previous connection — releases its TURN allocation
    // before we request the new one. Never more than 1 concurrent
    // allocation at a time.
    if (currentPc) {
        console.log("Closing previous peer connection...");
        currentPc.close();
        currentPc = null;
    }

    grid.innerHTML = "";

    status.innerText = "Connecting " + cameraId + "...";


    // =====================================================
    // WebRTC Peer Connection
    // =====================================================

    const pc = new RTCPeerConnection({
        iceServers: [
            {
                urls: "stun:stun.relay.metered.ca:80",
            },
            {
                urls: "turn:global.relay.metered.ca:80",
                username: "__TURN_USERNAME__",
                credential: "__TURN_CREDENTIAL__",
            },
            {
                urls: "turn:global.relay.metered.ca:80?transport=tcp",
                username: "__TURN_USERNAME__",
                credential: "__TURN_CREDENTIAL__",
            },
            {
                urls: "turn:global.relay.metered.ca:443",
                username: "__TURN_USERNAME__",
                credential: "__TURN_CREDENTIAL__",
            },
            {
                urls: "turns:global.relay.metered.ca:443?transport=tcp",
                username: "__TURN_USERNAME__",
                credential: "__TURN_CREDENTIAL__",
            },
        ],
    });

    currentPc = pc;

    pc.onconnectionstatechange = () => {

        console.log("WEBRTC CONNECTION STATE:", pc.connectionState);

        status.innerText =
            cameraId + " — Connection: " + pc.connectionState;
    };

    pc.oniceconnectionstatechange = () => {
        console.log("ICE CONNECTION STATE:", pc.iceConnectionState);
    };

    pc.ontrack = async (event) => {

        console.log("RECEIVED TRACK for camera:", cameraId);

        const tile = document.createElement("div");
        tile.className = "camera-tile";

        const label = document.createElement("div");
        label.className = "camera-label";
        label.innerText = cameraId;

        const video = document.createElement("video");
        video.autoplay = true;
        video.muted = true;
        video.playsInline = true;

        tile.appendChild(video);
        tile.appendChild(label);
        grid.appendChild(tile);

        const stream = new MediaStream();
        stream.addTrack(event.track);
        video.srcObject = stream;

        try {
            await video.play();
        } catch (error) {
            console.error("VIDEO PLAY FAILED:", cameraId, error);
        }
    };

    // Exactly one recvonly slot — server fills it with the requested camera
    pc.addTransceiver("video", { direction: "recvonly" });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    await waitForIceGatheringComplete(pc);

    const response = await fetch("/offer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type,
            camera_id: cameraId
        })
    });

    if (!response.ok) {
        status.innerText = "Offer failed: " + response.status;
        return;
    }

    const answer = await response.json();

    console.log("CONFIRMED CAMERA:", answer.camera_id);

    await pc.setRemoteDescription(answer);
}


async function setupControls() {

    const controls = document.getElementById("controls");

    const response = await fetch("/api/cameras");
    const data = await response.json();
    const cameraIds = data.camera_ids;

    cameraIds.forEach((cameraId) => {

        const btn = document.createElement("button");
        btn.innerText = cameraId;
        btn.dataset.cameraId = cameraId;
        btn.onclick = () => connect(cameraId);

        controls.appendChild(btn);
    });

    // Start on the first camera by default
    if (cameraIds.length > 0) {
        connect(cameraIds[0]);
    }
}


setupControls();

</script>

</body>

</html>
"""


def render_html():
    """
    Injects TURN credentials into the HTML template at request time,
    so they never sit hardcoded in source.
    """
    return (
        HTML_TEMPLATE
        .replace("__TURN_USERNAME__", TURN_USERNAME)
        .replace("__TURN_CREDENTIAL__", TURN_CREDENTIAL)
    )


# =========================================================
# HTTP Routes
# =========================================================

async def index(request):

    print()
    print("======================================")
    print("HTTP REQUEST: /")
    print("======================================")

    return web.Response(
        text=render_html(),
        content_type="text/html"
    )


# =========================================================
# Camera List
# =========================================================

async def cameras(request):

    return web.json_response(
        {"camera_ids": ALL_CAMERA_IDS}
    )


# =========================================================
# WebRTC Offer
# =========================================================

async def offer(request):

    print()
    print("======================================")
    print("HTTP REQUEST: /offer")
    print("======================================")


    params = await request.json()

    camera_id = params.get("camera_id", ALL_CAMERA_IDS[0])

    if camera_id not in ALL_CAMERA_IDS:
        camera_id = ALL_CAMERA_IDS[0]

    print(
        f"Requested camera: {camera_id}"
    )

    offer = RTCSessionDescription(
        sdp=params["sdp"],
        type=params["type"]
    )


    # -----------------------------------------------------
    # Create WebRTC Peer Connection
    # -----------------------------------------------------

    pc = RTCPeerConnection(
        configuration=ICE_CONFIG
    )


    pcs.add(pc)


    print()
    print("======================================")
    print("NEW WEBRTC CONNECTION")
    print("======================================")

    print(
        "Peer connection created"
    )


    # =====================================================
    # Connection State
    # =====================================================

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():

        print()
        print("--------------------------------------")
        print(
            "WEBRTC CONNECTION STATE:",
            pc.connectionState
        )
        print("--------------------------------------")


        if pc.connectionState in (
            "failed",
            "closed"
        ):

            print(
                "Closing failed/closed peer connection"
            )

            await pc.close()

            pcs.discard(pc)


    # =====================================================
    # ICE Connection State
    # =====================================================

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():

        print()
        print("--------------------------------------")
        print(
            f"[{time.strftime('%H:%M:%S')}] "
            f"ICE CONNECTION STATE: {pc.iceConnectionState}"
        )
        print("--------------------------------------")


    # =====================================================
    # ICE Gathering State
    # =====================================================

    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():

        print()
        print(
            "ICE GATHERING STATE:",
            pc.iceGatheringState
        )


    # =====================================================
    # Add GuardSense Video Track
    # =====================================================

    print(
        f"Adding video track for camera: {camera_id}"
    )

    track = GuardSenseVideoTrack(pipeline, camera_id)
    pc.addTrack(track)


    # =====================================================
    # Receive Browser Offer
    # =====================================================

    print(
        "Setting remote browser description..."
    )

    await pc.setRemoteDescription(
        offer
    )


    print(
        "Remote browser description set"
    )


    # =====================================================
    # Print Browser ICE Candidates
    # =====================================================

    print()
    print("======================================")
    print("BROWSER ICE CANDIDATES")
    print("======================================")


    browser_candidate_count = 0


    for line in offer.sdp.splitlines():

        if line.startswith("a=candidate:"):

            print(
                "BROWSER CANDIDATE:",
                line
            )

            browser_candidate_count += 1


    print(
        "Total browser candidates:",
        browser_candidate_count
    )

    print("======================================")


    # =====================================================
    # Create Answer
    # =====================================================

    print(
        "Creating WebRTC answer..."
    )

    answer = await pc.createAnswer()


    # =====================================================
    # Set Local Description
    # =====================================================

    await pc.setLocalDescription(
        answer
    )


    print(
        "Local WebRTC answer created"
    )


    # =====================================================
    # Print Server ICE Candidates
    # =====================================================

    print()
    print("======================================")
    print("SERVER ICE CANDIDATES")
    print("======================================")


    server_candidate_count = 0


    for line in pc.localDescription.sdp.splitlines():

        if line.startswith("a=candidate:"):

            print(
                "SERVER CANDIDATE:",
                line
            )

            server_candidate_count += 1


    print(
        "Total server candidates:",
        server_candidate_count
    )

    print("======================================")


    print()
    print(
        "Returning WebRTC answer to browser..."
    )


    # =====================================================
    # Return Answer
    # =====================================================

    return web.json_response(
        {
            "sdp":
                pc.localDescription.sdp,

            "type":
                pc.localDescription.type,

            "camera_id":
                camera_id
        }
    )


# =========================================================
# Shutdown
# =========================================================

async def shutdown(app):

    print()
    print("======================================")
    print("SHUTTING DOWN GUARDSENSE")
    print("======================================")


    # Stop pipeline
    pipeline.stop()


    # Close WebRTC connections
    await asyncio.gather(
        *[
            pc.close()
            for pc in pcs
        ],
        return_exceptions=True
    )


    pcs.clear()


    print(
        "GuardSense stopped"
    )


# =========================================================
# AIOHTTP Application
# =========================================================

app = web.Application()


app.router.add_get(
    "/",
    index
)


app.router.add_get(
    "/api/cameras",
    cameras
)


app.router.add_post(
    "/offer",
    offer
)


app.on_shutdown.append(
    shutdown
)


# =========================================================
# Main
# =========================================================

if __name__ == "__main__":

    print()
    print("======================================")
    print("STARTING GUARDSENSE")
    print("======================================")


    # Start camera + YOLO + ByteTrack
    pipeline.start()


    # Start HTTP + WebRTC server
    web.run_app(
        app,
        host="0.0.0.0",
        port=8080
    )