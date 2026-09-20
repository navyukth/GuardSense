"""
/alerts page: "what happened today" - alert history for any day, filterable
by camera and person. Alerts are stored in SQLite by the relay, so this
survives restarts and deploys (the live panel on the main page is just the
latest 100).
"""

import json

ALERTS_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuardSense — Alert history</title>
<style>
body { margin: 0; background: #111; color: white; font-family: Arial, sans-serif; }
#topbar { display: flex; align-items: center; justify-content: center; gap: 16px; padding: 12px 0; }
h1 { font-size: 20px; margin: 0; }
a#back {
    color: #999; font-size: 13px; text-decoration: none;
    border: 1px solid #444; padding: 4px 10px; border-radius: 4px;
}
a#back:hover { color: white; border-color: #777; }
#wrap { max-width: 760px; margin: 0 auto; padding: 0 16px 40px; }
#filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; justify-content: center; margin-bottom: 16px; }
select, input, button {
    background: #222; color: white; border: 1px solid #444;
    border-radius: 4px; padding: 6px 8px; font-size: 13px;
}
button { cursor: pointer; background: #333; border-color: #555; }
button:hover { background: #444; }
#summary { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; justify-content: center; }
.chip {
    background: #1a1a1a; border: 1px solid #333; border-radius: 14px;
    padding: 4px 12px; font-size: 12px; color: #ccc; cursor: pointer;
}
.chip.active { border-color: #2a7; color: white; }
.chip b { color: #2a7; margin-left: 4px; }
#list { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 4px 14px; }
.row { display: flex; justify-content: space-between; gap: 10px; padding: 8px 0; border-bottom: 1px solid #2a2a2a; font-size: 13px; }
.row:last-child { border-bottom: none; }
.row .who { font-weight: bold; }
.row .who.unknown { color: #f9a; }
.row .meta { color: #999; white-space: nowrap; }
.empty { color: #666; padding: 16px 0; text-align: center; font-size: 13px; }
</style>
</head>
<body>

<div id="topbar">
    <h1>Alert history</h1>
    <a id="back" href="/">Back to feeds</a>
</div>

<div id="wrap">
    <div id="filters">
        <button id="prev">&larr;</button>
        <input type="date" id="date">
        <button id="next">&rarr;</button>
        <button id="today">Today</button>
        <select id="camera"><option value="">All cameras</option></select>
    </div>

    <div id="summary"></div>
    <div id="list"><div class="empty">Loading...</div></div>
</div>

<script>

const CAMERAS = __CAMERAS_JSON__;

const dateEl = document.getElementById("date");
const cameraEl = document.getElementById("camera");
let personFilter = null;   // label chip currently selected
let lastAlerts = [];

CAMERAS.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    cameraEl.appendChild(opt);
});

function localDateString(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

function shiftDay(delta) {
    const [y, m, d] = dateEl.value.split("-").map(Number);
    const next = new Date(y, m - 1, d + delta);
    dateEl.value = localDateString(next);
    load();
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.innerText = text == null ? "" : String(text);
    return div.innerHTML;
}

function isUnknown(a) {
    return a.person_id == null;
}

function render() {
    const summaryEl = document.getElementById("summary");
    const listEl = document.getElementById("list");

    const counts = {};
    lastAlerts.forEach((a) => {
        const key = isUnknown(a) ? "Unknown" : a.label;
        counts[key] = (counts[key] || 0) + 1;
    });

    const total = lastAlerts.length;
    summaryEl.innerHTML =
        '<span class="chip ' + (personFilter === null ? "active" : "") + '" data-person="">All<b>' + total + "</b></span>" +
        Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([name, n]) =>
            '<span class="chip ' + (personFilter === name ? "active" : "") + '" data-person="' + escapeHtml(name) + '">' +
            escapeHtml(name) + "<b>" + n + "</b></span>"
        ).join("");

    summaryEl.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
            personFilter = chip.dataset.person === "" ? null : chip.dataset.person;
            render();
        });
    });

    const shown = lastAlerts.filter((a) =>
        personFilter === null || (isUnknown(a) ? "Unknown" : a.label) === personFilter
    );

    if (shown.length === 0) {
        listEl.innerHTML = '<div class="empty">No alerts for this selection.</div>';
        return;
    }

    listEl.innerHTML = shown.map((a) => {
        const time = a.timestamp.split(" ")[1] || a.timestamp;
        return '<div class="row"><span class="who ' + (isUnknown(a) ? "unknown" : "") + '">' +
            escapeHtml(a.label) + '</span><span class="meta">' +
            escapeHtml(a.camera_id) + " · " + escapeHtml(time) + "</span></div>";
    }).join("");
}

async function load() {
    const params = new URLSearchParams({ date: dateEl.value, limit: "2000" });
    if (cameraEl.value) params.set("camera", cameraEl.value);

    try {
        const response = await fetch("/api/alerts?" + params.toString());
        const data = await response.json();
        lastAlerts = data.alerts;
        render();
    } catch (e) {
        document.getElementById("list").innerHTML = '<div class="empty">Failed to load alerts.</div>';
    }
}

document.getElementById("prev").addEventListener("click", () => shiftDay(-1));
document.getElementById("next").addEventListener("click", () => shiftDay(1));
document.getElementById("today").addEventListener("click", () => { dateEl.value = localDateString(new Date()); load(); });
dateEl.addEventListener("change", load);
cameraEl.addEventListener("change", load);

dateEl.value = localDateString(new Date());
load();
setInterval(() => {
    // only auto-refresh when looking at today
    if (dateEl.value === localDateString(new Date())) load();
}, 10000);

</script>

</body>
</html>
"""


def render_alerts_html(camera_ids):
    return ALERTS_HTML.replace("__CAMERAS_JSON__", json.dumps(list(camera_ids)))
