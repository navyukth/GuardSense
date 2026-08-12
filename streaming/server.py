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

    def __init__(self, pipeline):
        super().__init__()

        self.pipeline = pipeline

    async def recv(self):

        # WebRTC timestamp
        pts, time_base = await self.next_timestamp()

        frame = None

        # Wait until GuardSense has produced a frame
        while frame is None:

            frame = self.pipeline.get_latest_frame()

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

video {
    width: 90%;
    max-width: 960px;
    background: black;
}

</style>

</head>

<body>

<h1>GuardSense Live</h1>

<div id="status">
    Starting WebRTC...
</div>

<video
    id="video"
    autoplay
    muted
    playsinline
    controls>
</video>


<script>

async function waitForIceGatheringComplete(pc) {

    console.log(
        "Waiting for ICE gathering to complete..."
    );

    if (pc.iceGatheringState === "complete") {

        console.log(
            "ICE gathering already complete"
        );

        return;
    }

    await new Promise((resolve) => {

        const checkState = () => {

            console.log(
                "ICE gathering state:",
                pc.iceGatheringState
            );

            if (pc.iceGatheringState === "complete") {

                pc.removeEventListener(
                    "icegatheringstatechange",
                    checkState
                );

                resolve();
            }
        };

        pc.addEventListener(
            "icegatheringstatechange",
            checkState
        );

    });

    console.log(
        "ICE gathering completed"
    );
}


async function start() {

    console.log("======================================");
    console.log("Starting GuardSense WebRTC");
    console.log("======================================");

    const status =
        document.getElementById("status");

    const video =
        document.getElementById("video");


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


    // =====================================================
    // Connection State
    // =====================================================

    pc.onconnectionstatechange = () => {

        console.log(
            "WEBRTC CONNECTION STATE:",
            pc.connectionState
        );

        status.innerText =
            "Connection: " +
            pc.connectionState;
    };


    // =====================================================
    // ICE Connection State
    // =====================================================

    pc.oniceconnectionstatechange = () => {

        console.log(
            "ICE CONNECTION STATE:",
            pc.iceConnectionState
        );

        status.innerText =
            "ICE: " +
            pc.iceConnectionState;
    };


    // =====================================================
    // ICE Gathering State
    // =====================================================

    pc.onicegatheringstatechange = () => {

        console.log(
            "ICE GATHERING STATE:",
            pc.iceGatheringState
        );
    };


    // =====================================================
    // ICE Candidate
    // =====================================================

    pc.onicecandidate = (event) => {

        if (event.candidate) {

            console.log(
                "BROWSER ICE CANDIDATE:",
                event.candidate.candidate
            );

        } else {

            console.log(
                "BROWSER ICE CANDIDATE GATHERING COMPLETE"
            );
        }
    };


    // =====================================================
    // WebRTC Track
    // =====================================================

    pc.ontrack = async (event) => {

        console.log(
            "RECEIVED WEBRTC TRACK:",
            event.track.kind
        );

        const stream =
            new MediaStream();

        stream.addTrack(
            event.track
        );

        video.srcObject =
            stream;

        try {

            await video.play();

            console.log(
                "VIDEO PLAYBACK STARTED"
            );

        } catch (error) {

            console.error(
                "VIDEO PLAY FAILED:",
                error
            );
        }
    };


    // =====================================================
    // Receive Video
    // =====================================================

    pc.addTransceiver(
        "video",
        {
            direction: "recvonly"
        }
    );


    // =====================================================
    // Create Offer
    // =====================================================

    console.log(
        "Creating WebRTC offer..."
    );

    const offer =
        await pc.createOffer();


    // =====================================================
    // Set Local Description
    // =====================================================

    await pc.setLocalDescription(
        offer
    );


    console.log(
        "LOCAL SDP CREATED"
    );


    // =====================================================
    // WAIT FOR ICE GATHERING
    // =====================================================

    await waitForIceGatheringComplete(
        pc
    );


    // =====================================================
    // Print Final Local SDP
    // =====================================================

    console.log(
        "======================================"
    );

    console.log(
        "FINAL LOCAL SDP"
    );

    console.log(
        "======================================"
    );

    console.log(
        pc.localDescription.sdp
    );


    // =====================================================
    // Send Offer to GuardSense
    // =====================================================

    console.log(
        "Sending offer to /offer..."
    );


    const response =
        await fetch(
            "/offer",
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body: JSON.stringify({

                    sdp:
                        pc.localDescription.sdp,

                    type:
                        pc.localDescription.type

                })
            }
        );


    if (!response.ok) {

        console.error(
            "OFFER REQUEST FAILED:",
            response.status
        );

        status.innerText =
            "Offer failed: " +
            response.status;

        return;
    }


    // =====================================================
    // Receive Answer
    // =====================================================

    const answer =
        await response.json();


    console.log(
        "RECEIVED WEBRTC ANSWER"
    );


    console.log(
        "REMOTE SDP:"
    );

    console.log(
        answer.sdp
    );


    // =====================================================
    // Set Remote Description
    // =====================================================

    await pc.setRemoteDescription(
        answer
    );


    console.log(
        "REMOTE DESCRIPTION SET"
    );

    console.log(
        "Waiting for ICE connection..."
    );
}


start();

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
# WebRTC Offer
# =========================================================

async def offer(request):

    print()
    print("======================================")
    print("HTTP REQUEST: /offer")
    print("======================================")


    params = await request.json()


    print(
        "Received SDP offer from browser"
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
        "Adding GuardSense video track..."
    )

    track = GuardSenseVideoTrack(
        pipeline
    )

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
                pc.localDescription.type
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