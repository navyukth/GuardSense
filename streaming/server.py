import asyncio

import cv2

from aiohttp import web

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)

from av import VideoFrame

from streaming.guardsense_pipeline import (
    GuardSensePipeline
)


# ---------------------------------------------------------
# GuardSense
# ---------------------------------------------------------

pipeline = GuardSensePipeline()

pcs = set()


# ---------------------------------------------------------
# WebRTC Track
# ---------------------------------------------------------

class GuardSenseVideoTrack(VideoStreamTrack):

    def __init__(self, pipeline):

        super().__init__()

        self.pipeline = pipeline

    async def recv(self):

        pts, time_base = await self.next_timestamp()

        frame = None

        # Wait until GuardSense produces a frame
        while frame is None:

            frame = self.pipeline.get_latest_frame()

            if frame is None:

                await asyncio.sleep(0.01)

        # OpenCV BGR -> RGB
        frame = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2RGB
        )

        video_frame = VideoFrame.from_ndarray(
            frame,
            format="rgb24"
        )

        video_frame.pts = pts
        video_frame.time_base = time_base

        return video_frame


# ---------------------------------------------------------
# HTML
# ---------------------------------------------------------

HTML = """
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<title>GuardSense Live</title>

<style>

body {
    margin: 0;
    background: #111;
    color: white;
    font-family: Arial;
    text-align: center;
}

h1 {
    margin: 20px;
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

    const pc = new RTCPeerConnection();

    const video = document.getElementById("video");

    // -----------------------------
    // WebRTC diagnostics
    // -----------------------------
    pc.onconnectionstatechange = () => {

        console.log(
            "Connection state:",
            pc.connectionState
        );

        document.getElementById("status").innerText =
            "Connection: " + pc.connectionState;
    };


    pc.oniceconnectionstatechange = () => {

        console.log(
            "ICE state:",
            pc.iceConnectionState
        );

        document.getElementById("status").innerText =   
            "ICE: " + pc.iceConnectionState;
    };


    pc.onicegatheringstatechange = () => {

        console.log(
            "ICE gathering:",
            pc.iceGatheringState
        );
    };

    pc.ontrack = async (event) => {

        console.log(
            "Received WebRTC track:",
            event.track.kind
        );

        // Attach the actual track directly
        const stream = new MediaStream();

        stream.addTrack(event.track);

        video.srcObject = stream;

        try {
            await video.play();
            console.log("Video playback started");
        } catch (error) {
            console.error(
                "Video play failed:",
                error
            );
        }
    };

    // We want to RECEIVE video
    pc.addTransceiver(
        "video",
        {
            direction: "recvonly"
        }
    );

    // -----------------------------
    // Create offer
    // -----------------------------

    const offer = await pc.createOffer();

    await pc.setLocalDescription(offer);

    console.log(
        "Local SDP created"
    );

    // -----------------------------
    // Send offer to Python
    // -----------------------------

    const response = await fetch(
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

        return;
    }

    const answer = await response.json();

    console.log(
        "Received WebRTC answer"
    );

    // -----------------------------
    // Set answer
    // -----------------------------

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


# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------

async def index(request):

    return web.Response(
        text=HTML,
        content_type="text/html"
    )


async def offer(request):

    params = await request.json()

    offer = RTCSessionDescription(
        sdp=params["sdp"],
        type=params["type"]
    )

    pc = RTCPeerConnection()

    pcs.add(pc)

    print(
        "New WebRTC connection"
    )

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

    track = GuardSenseVideoTrack(
        pipeline
    )

    pc.addTrack(track)

    await pc.setRemoteDescription(
        offer
    )

    answer = await pc.createAnswer()

    await pc.setLocalDescription(
        answer
    )

    return web.json_response(
        {
            "sdp":
                pc.localDescription.sdp,

            "type":
                pc.localDescription.type
        }
    )


# ---------------------------------------------------------
# Shutdown
# ---------------------------------------------------------

async def shutdown(app):

    print(
        "Shutting down..."
    )

    pipeline.stop()

    await asyncio.gather(
        *[
            pc.close()
            for pc in pcs
        ],
        return_exceptions=True
    )

    pcs.clear()


# ---------------------------------------------------------
# App
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

if __name__ == "__main__":

    pipeline.start()

    web.run_app(
        app,
        host="0.0.0.0",
        port=8080
    )