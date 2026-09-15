"""
/logs page: live-ish view of capture_node.py's log lines (camera
connects/reconnects, model loads, relay connection state, errors),
shipped up over the ingest websocket so you don't need SSH access to
whichever machine (laptop or Pi5) happens to be running the pipeline.
"""

LOGS_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuardSense — Logs</title>
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
#controls {
    display: flex;
    justify-content: center;
    gap: 8px;
    padding: 0 16px 12px;
}
#controls label {
    font-size: 12px;
    color: #999;
    display: flex;
    align-items: center;
    gap: 4px;
}
#wrap { max-width: 900px; margin: 0 auto; padding: 0 16px 40px; }
#console {
    background: #0a0a0a;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 12px 14px;
    height: 70vh;
    overflow-y: auto;
    font-family: "Consolas", "Menlo", monospace;
    font-size: 12.5px;
    line-height: 1.6;
}
.line { white-space: pre-wrap; word-break: break-word; }
.ts { color: #666; }
.lvl-INFO { color: #8fc; }
.lvl-WARNING { color: #fc6; }
.lvl-ERROR { color: #f66; }
.lvl-DEBUG { color: #888; }
.empty { color: #666; }
</style>
</head>
<body>

<div id="topbar">
    <h1>Logs</h1>
    <a id="back" href="/">Back to feeds</a>
</div>

<div id="controls">
    <label><input type="checkbox" id="autoscroll" checked> Auto-scroll</label>
</div>

<div id="wrap">
    <div id="console"><div class="empty">Loading...</div></div>
</div>

<script>

const consoleEl = document.getElementById("console");
const autoscrollEl = document.getElementById("autoscroll");

function escapeHtml(text) {
    const div = document.createElement("div");
    div.innerText = text;
    return div.innerHTML;
}

function renderLine(entry) {
    const div = document.createElement("div");
    div.className = "line";
    div.innerHTML =
        '<span class="ts">' + escapeHtml(entry.timestamp) + '</span> ' +
        '<span class="lvl-' + escapeHtml(entry.level) + '">[' + escapeHtml(entry.level) + ']</span> ' +
        escapeHtml(entry.message);
    return div;
}

async function refresh() {
    try {
        const response = await fetch("/api/logs");
        const data = await response.json();

        if (data.logs.length === 0) {
            consoleEl.innerHTML = '<div class="empty">No log lines yet.</div>';
            return;
        }

        const wasAtBottom = consoleEl.scrollTop + consoleEl.clientHeight >= consoleEl.scrollHeight - 20;

        consoleEl.innerHTML = "";
        for (const entry of data.logs) {
            consoleEl.appendChild(renderLine(entry));
        }

        if (autoscrollEl.checked || wasAtBottom) {
            consoleEl.scrollTop = consoleEl.scrollHeight;
        }
    } catch (error) {
        consoleEl.innerHTML = '<div class="empty">Failed to load logs.</div>';
    }
}

refresh();
setInterval(refresh, 3000);

</script>

</body>
</html>
"""


def render_logs_html():
    return LOGS_HTML
