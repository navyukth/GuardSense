"""
Shared webpage/login templates + auth middleware used by BOTH deployment
modes:

  - streaming/server.py       — all-in-one (capture + detect + serve),
                                 used for local/LAN testing on the laptop
  - streaming/relay_server.py — Pi5 mode: serves the page + terminates
                                 WebRTC to the browser, using frames/alerts
                                 pushed in from the laptop's capture node

Keeping this in one place means the page and the login flow can't drift
between the two modes.
"""

import hmac
import json
import secrets

from aiohttp import web

SESSION_COOKIE = "gs_session"


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

#topbar {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
}

#logout {
    color: #999;
    font-size: 13px;
    text-decoration: none;
    border: 1px solid #444;
    padding: 4px 10px;
    border-radius: 4px;
}

#logout:hover {
    color: white;
    border-color: #777;
}

#panels {
    display: flex;
    justify-content: center;
    gap: 16px;
    flex-wrap: wrap;
    padding: 8px 16px 32px;
}

.panel {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 12px 16px;
    width: 360px;
    max-width: 90%;
    text-align: left;
}

.panel h2 {
    margin: 0 0 10px;
    font-size: 15px;
    color: #aaa;
}

.alert-row {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid #2a2a2a;
    font-size: 13px;
}

.alert-row:last-child {
    border-bottom: none;
}

.status-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 13px;
}

</style>

</head>

<body>

<div id="topbar">
    <h1>GuardSense Live</h1>
    <a id="logout" href="/people">People</a>
    <a id="logout" href="/settings">Settings</a>
    <a id="logout" href="/logout">Log out</a>
</div>

<div id="status">
    Starting WebRTC...
</div>

<div id="controls"></div>

<div id="grid"></div>

<div id="panels">

    <div class="panel">
        <h2>Alerts</h2>
        <div id="alerts">Loading...</div>
    </div>

    <div class="panel">
        <h2>Server</h2>
        <div id="server-info">Loading...</div>
    </div>

</div>


<script>

const ICE_SERVERS = __ICE_SERVERS_JSON__;

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
        iceServers: ICE_SERVERS,
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


function escapeHtml(text) {
    const div = document.createElement("div");
    div.innerText = text;
    return div.innerHTML;
}

async function refreshAlerts() {

    const el = document.getElementById("alerts");

    try {

        const response = await fetch("/api/alerts");
        const data = await response.json();

        if (data.alerts.length === 0) {
            el.innerHTML = "No sightings yet.";
            return;
        }

        el.innerHTML = data.alerts.map((a) => {
            const who = a.label || ("Person #" + a.person_id);
            return (
                '<div class="alert-row">' +
                "<span>" + escapeHtml(who) + " — " + escapeHtml(a.camera_id) + "</span>" +
                "<span>" + escapeHtml(a.timestamp) + "</span>" +
                "</div>"
            );
        }).join("");

    } catch (error) {
        el.innerHTML = "Failed to load alerts.";
    }
}

async function refreshServerInfo() {

    const el = document.getElementById("server-info");

    try {

        const response = await fetch("/api/status");
        const data = await response.json();

        const rows = [
            ["Device", data.device],
            ["Cameras", data.camera_ids.join(", ")],
            ["Active camera", activeCameraId || "-"],
            ["Uptime", data.uptime_human],
            ["Viewers", data.active_connections],
        ];

        el.innerHTML = rows.map(([label, value]) =>
            '<div class="status-row"><span>' + escapeHtml(label) + '</span><span>' +
            escapeHtml(String(value)) + "</span></div>"
        ).join("");

    } catch (error) {
        el.innerHTML = "Failed to load server info.";
    }
}

setupControls();

refreshAlerts();
refreshServerInfo();

setInterval(refreshAlerts, 5000);
setInterval(refreshServerInfo, 5000);

</script>

</body>

</html>
"""


def render_html(ice_servers):
    """
    Injects the ICE server list as-is (used to build both the aiortc
    RTCConfiguration and this JSON blob, so they can't drift).
    """
    return HTML_TEMPLATE.replace(
        "__ICE_SERVERS_JSON__",
        json.dumps(ice_servers)
    )


# =========================================================
# Login (single admin password, in-memory session cookie —
# this is a one-person home tool, not a multi-user app)
# =========================================================

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuardSense — Log in</title>
<style>
body {
    margin: 0;
    height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #111;
    color: white;
    font-family: Arial, sans-serif;
}
form {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 32px;
    width: 260px;
    text-align: center;
}
h1 {
    font-size: 18px;
    margin: 0 0 20px;
}
input {
    width: 100%;
    box-sizing: border-box;
    padding: 10px;
    margin-bottom: 12px;
    border-radius: 4px;
    border: 1px solid #444;
    background: #222;
    color: white;
    font-size: 14px;
}
button {
    width: 100%;
    padding: 10px;
    border-radius: 4px;
    border: none;
    background: #2a7;
    color: white;
    font-size: 14px;
    cursor: pointer;
}
.error {
    color: #f66;
    font-size: 13px;
    margin-bottom: 12px;
}
</style>
</head>
<body>
<form method="POST" action="/login">
<h1>GuardSense</h1>
__ERROR__
<input type="password" name="password" placeholder="Password" autofocus>
<button type="submit">Log in</button>
</form>
</body>
</html>
"""


def render_login_html(error=False):
    error_html = '<div class="error">Wrong password</div>' if error else ""
    return LOGIN_HTML.replace("__ERROR__", error_html)


class Auth:
    """
    Bundles the auth middleware + login/logout handlers around one admin
    password and its own in-memory session set. Each process (laptop
    all-in-one server, Pi5 relay) gets its own instance/sessions.
    """

    def __init__(self, admin_password, exempt_paths=()):
        self.admin_password = admin_password
        self.valid_sessions = set()
        # /login always exempt; callers add routes with their own auth
        # (e.g. the Pi5 relay's token-authenticated /ws/ingest).
        self.exempt_paths = {"/login", *exempt_paths}

    @web.middleware
    async def middleware(self, request, handler):

        if request.path in self.exempt_paths:
            return await handler(request)

        token = request.cookies.get(SESSION_COOKIE)

        if token not in self.valid_sessions:
            raise web.HTTPFound("/login")

        return await handler(request)

    async def login_page(self, request):
        return web.Response(text=render_login_html(), content_type="text/html")

    async def login_submit(self, request):

        form = await request.post()
        password = form.get("password", "")

        if not hmac.compare_digest(password, self.admin_password):
            return web.Response(
                text=render_login_html(error=True),
                content_type="text/html",
                status=401
            )

        token = secrets.token_urlsafe(32)
        self.valid_sessions.add(token)

        response = web.HTTPFound("/")
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="Lax"
        )
        return response

    async def logout(self, request):

        token = request.cookies.get(SESSION_COOKIE)
        self.valid_sessions.discard(token)

        response = web.HTTPFound("/login")
        response.del_cookie(SESSION_COOKIE)
        return response


# =========================================================
# Settings — view-only panel for the admin login password and the
# other shared secrets (TURN credential, relay ingest token). All of
# these live in the relay's .env; this page just makes them visible
# with a reveal toggle so you don't have to SSH in to check them.
# =========================================================

SETTINGS_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuardSense — Settings</title>
<style>
body {
    margin: 0;
    background: #111;
    color: white;
    font-family: Arial, sans-serif;
}
#topbar {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    padding: 12px 0;
}
h1 { font-size: 20px; margin: 0; }
a#back {
    color: #999;
    font-size: 13px;
    text-decoration: none;
    border: 1px solid #444;
    padding: 4px 10px;
    border-radius: 4px;
}
a#back:hover { color: white; border-color: #777; }
#wrap {
    max-width: 480px;
    margin: 0 auto;
    padding: 0 16px 40px;
}
.panel {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 16px 18px;
    margin-bottom: 16px;
}
.panel h2 {
    margin: 0 0 12px;
    font-size: 14px;
    color: #aaa;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.field { margin-bottom: 12px; }
.field:last-child { margin-bottom: 0; }
.field label {
    display: block;
    font-size: 12px;
    color: #888;
    margin-bottom: 4px;
}
.field-row {
    display: flex;
    gap: 8px;
    align-items: center;
}
.field-row input {
    flex: 1;
    box-sizing: border-box;
    padding: 8px 10px;
    border-radius: 4px;
    border: 1px solid #444;
    background: #222;
    color: white;
    font-size: 13px;
    font-family: monospace;
}
.field-row button {
    background: #333;
    color: white;
    border: 1px solid #555;
    padding: 8px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    white-space: nowrap;
}
.field-row button:hover { background: #444; }
.note {
    font-size: 12px;
    color: #777;
    margin-top: 4px;
}
</style>
</head>
<body>

<div id="topbar">
    <h1>Settings</h1>
    <a id="back" href="/">Back to feeds</a>
</div>

<div id="wrap">

    <div class="panel">
        <h2>Web UI login</h2>
        <div class="field">
            <label>Admin password</label>
            <div class="field-row">
                <input type="password" value="__ADMIN_PASSWORD__" readonly data-secret>
                <button type="button" data-toggle>Show</button>
            </div>
            <div class="note">
                Change it by editing ADMIN_PASSWORD in the relay's .env and
                restarting the container.
            </div>
        </div>
    </div>

    <div class="panel">
        <h2>TURN / STUN (coturn)</h2>
        <div class="field">
            <label>Host</label>
            <div class="field-row">
                <input type="text" value="__TURN_HOST__" readonly>
            </div>
        </div>
        <div class="field">
            <label>Username</label>
            <div class="field-row">
                <input type="text" value="__TURN_USERNAME__" readonly>
            </div>
        </div>
        <div class="field">
            <label>Credential</label>
            <div class="field-row">
                <input type="password" value="__TURN_CREDENTIAL__" readonly data-secret>
                <button type="button" data-toggle>Show</button>
            </div>
        </div>
    </div>

    <div class="panel">
        <h2>Capture node ingest</h2>
        <div class="field">
            <label>Ingest token</label>
            <div class="field-row">
                <input type="password" value="__RELAY_INGEST_TOKEN__" readonly data-secret>
                <button type="button" data-toggle>Show</button>
            </div>
            <div class="note">The laptop's capture_node.py authenticates with this.</div>
        </div>
    </div>

    <div class="panel">
        <h2>Cameras</h2>
        <div class="field">
            <label>Configured camera IDs</label>
            <div class="field-row">
                <input type="text" value="__CAMERA_IDS__" readonly>
            </div>
        </div>
    </div>

</div>

<script>
document.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
        const input = btn.previousElementSibling;
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        btn.innerText = showing ? "Show" : "Hide";
    });
});
</script>

</body>
</html>
"""


def render_settings_html(config):
    html = SETTINGS_HTML
    for key, value in config.items():
        html = html.replace(f"__{key}__", str(value))
    return html
