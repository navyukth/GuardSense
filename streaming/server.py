import asyncio

import cv2

from aiohttp import web

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
# GuardSense Pipeline
# =========================================================

pipeline = GuardSensePipeline()

pcs = set()


# =========================================================
# STUN / ICE Configuration
# =========================================================

ICE_CONFIG = RTCConfiguration(
    iceServers=[
        RTCIceServer(
            urls="stun:stun.l.google.com:19302"
        )
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
# HTML
# =========================================================

HTML = """
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

async function start() {

    console.log("Starting GuardSense WebRTC...");

    const status =
        document.getElementById("status");

    const video =
        document.getElementById("video");


    // =====================================================
    // WebRTC Peer Connection
    // =====================================================

    const pc = new RTCPeerConnection();


    // =====================================================
    // Connection State
    // =====================================================

    pc.onconnectionstatechange = () => {

        console.log(
            "Connection state:",
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
            "ICE state:",
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
            "ICE gathering:",
            pc.iceGatheringState
        );
    };


    // =====================================================
    // ICE Candidate
    // =====================================================

    pc.onicecandidate = (event) => {

        if (event.candidate) {

            console.log(
                "ICE candidate:",
                event.candidate.candidate
            );

        } else {

            console.log(
                "ICE candidate gathering complete"
            );
        }
    };


    // =====================================================
    // WebRTC Track
    // =====================================================

    pc.ontrack = async (event) => {

        console.log(
            "Received WebRTC track:",
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
                "Video playback started"
            );

        } catch (error) {

            console.error(
                "Video play failed:",
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

    const offer =
        await pc.createOffer();


    await pc.setLocalDescription(
        offer
    );


    console.log(
        "Local SDP created"
    );


    // =====================================================
    // Send Offer to GuardSense
    // =====================================================

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
            "Offer request failed:",
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
        "Received WebRTC answer"
    );


    // =====================================================
    // Set Remote Description
    // =====================================================

    await pc.setRemoteDescription(
        answer
    );


    console.log(
        "Remote description set"
    );
}


start();

</script>

</body>

</html>
"""


# =========================================================
# HTTP Routes
# =========================================================

async def index(request):

    return web.Response(
        text=HTML,
        content_type="text/html"
    )


# =========================================================
# WebRTC Offer
# =========================================================

async def offer(request):

    params = await request.json()


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
    print("New WebRTC connection")
    print("======================================")


    # =====================================================
    # Connection State
    # =====================================================

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():

        print(
            "WebRTC state:",
            pc.connectionState
        )


        if pc.connectionState in (
            "failed",
            "closed"
        ):

            await pc.close()

            pcs.discard(pc)


    # =====================================================
    # ICE Connection State
    # =====================================================

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():

        print(
            "ICE state:",
            pc.iceConnectionState
        )


    # =====================================================
    # ICE Gathering State
    # =====================================================

    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():

        print(
            "ICE gathering state:",
            pc.iceGatheringState
        )


    # =====================================================
    # Add GuardSense Video Track
    # =====================================================

    track = GuardSenseVideoTrack(
        pipeline
    )


    pc.addTrack(track)


    # =====================================================
    # Receive Browser Offer
    # =====================================================

    await pc.setRemoteDescription(
        offer
    )


    # =====================================================
    # Create Answer
    # =====================================================

    answer = await pc.createAnswer()


    await pc.setLocalDescription(
        answer
    )


    print(
        "Local WebRTC answer created"
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
    print("Shutting down GuardSense...")


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
    print("Starting GuardSense")
    print("======================================")


    # Start camera + YOLO + ByteTrack
    pipeline.start()


    # Start HTTP + WebRTC server
    web.run_app(
        app,
        host="0.0.0.0",
        port=8080
    )