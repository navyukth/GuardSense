"""
/people page: review unlabeled ByteTrack sightings (crops the capture node
shipped in but nobody's named yet), name them or merge them into an
existing person, review/delete outlier crops, and manage named people.
Plain HTML/CSS/vanilla JS, served by streaming/relay_server.py.
"""

PEOPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GuardSense — People</title>
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
    max-width: 960px;
    margin: 0 auto;
    padding: 0 16px 40px;
}
section { margin-bottom: 32px; }
section h2 {
    font-size: 14px;
    color: #aaa;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin: 0 0 12px;
}
.empty {
    color: #666;
    font-size: 13px;
    padding: 12px 0;
}
.card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px;
}
.card {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 12px;
}
.thumb-pair {
    display: flex;
    gap: 6px;
    margin-bottom: 10px;
}
.thumb-pair .thumb {
    flex: 1;
    position: relative;
}
.thumb img {
    width: 100%;
    height: 110px;
    object-fit: cover;
    border-radius: 4px;
    background: #000;
    display: block;
}
.thumb .tag {
    position: absolute;
    bottom: 4px;
    left: 4px;
    font-size: 10px;
    background: rgba(0,0,0,0.7);
    padding: 1px 5px;
    border-radius: 3px;
}
.card .meta {
    font-size: 12px;
    color: #999;
    margin-bottom: 8px;
}
.card .meta strong { color: #ddd; }
select, button, input[type=text] {
    background: #222;
    color: white;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 6px 8px;
    font-size: 12px;
}
button {
    cursor: pointer;
    background: #333;
    border-color: #555;
}
button:hover { background: #444; }
button.danger { background: #3a1a1a; border-color: #663; color: #f88; }
button.danger:hover { background: #522; }
.card-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
}
.card-actions select { flex: 1; min-width: 110px; }

/* ---- Modal ---- */
#modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    z-index: 10;
}
#modal-backdrop[hidden] { display: none; }
#modal {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 20px;
    width: 100%;
    max-width: 680px;
    max-height: 85vh;
    overflow-y: auto;
}
#modal h3 { margin: 0 0 4px; font-size: 16px; }
#modal .sub { color: #888; font-size: 12px; margin-bottom: 14px; }
#modal .crop-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
    gap: 8px;
    margin-bottom: 16px;
}
.crop-cell {
    position: relative;
}
.crop-cell img {
    width: 100%;
    height: 90px;
    object-fit: cover;
    border-radius: 4px;
    border: 2px solid transparent;
}
.crop-cell.excluded img {
    opacity: 0.3;
    border-color: #a44;
}
.crop-cell .x {
    position: absolute;
    top: 2px;
    right: 2px;
    background: rgba(0,0,0,0.75);
    color: #f88;
    border: none;
    border-radius: 3px;
    width: 20px;
    height: 20px;
    font-size: 12px;
    line-height: 1;
    cursor: pointer;
}
#modal .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
}
</style>
</head>
<body>

<div id="topbar">
    <h1>People</h1>
    <a id="back" href="/">Back to feeds</a>
</div>

<div id="wrap">

    <section>
        <h2>Unlabeled sightings</h2>
        <div id="unassigned" class="card-grid"></div>
        <div id="unassigned-empty" class="empty" hidden>No unlabeled sightings right now.</div>
    </section>

    <section>
        <h2>Known people</h2>
        <div id="people" class="card-grid"></div>
        <div id="people-empty" class="empty" hidden>No named people yet.</div>
    </section>

</div>

<div id="modal-backdrop" hidden>
    <div id="modal">
        <h3 id="modal-title"></h3>
        <div class="sub" id="modal-sub"></div>
        <div class="crop-grid" id="modal-crops"></div>
        <div class="actions">
            <button id="modal-cancel">Cancel</button>
            <button id="modal-confirm">Confirm merge</button>
        </div>
    </div>
</div>

<script>

let peopleCache = [];
let pendingMerge = null; // { session_key OR from_id, into_id, name, crops: [...], excluded: Set }

function escapeHtml(text) {
    const div = document.createElement("div");
    div.innerText = text == null ? "" : String(text);
    return div.innerHTML;
}

async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error("Request failed (" + response.status + "): " + text);
    }
    if (response.status === 204) return null;
    return response.json();
}

async function loadAll() {
    const [unassignedData, peopleData] = await Promise.all([
        api("/api/people/unassigned"),
        api("/api/people"),
    ]);
    peopleCache = peopleData.people;
    renderUnassigned(unassignedData.groups);
    renderPeople(peopleData.people);
}

function personSelectOptions(excludeId) {
    let html = '<option value="">+ New person...</option>';
    for (const p of peopleCache) {
        if (p.id === excludeId) continue;
        html += `<option value="${p.id}">${escapeHtml(p.name)} (${p.crop_count})</option>`;
    }
    return html;
}

function renderUnassigned(groups) {
    const el = document.getElementById("unassigned");
    const emptyEl = document.getElementById("unassigned-empty");

    if (groups.length === 0) {
        el.innerHTML = "";
        emptyEl.hidden = false;
        return;
    }
    emptyEl.hidden = true;

    el.innerHTML = groups.map((g) => `
        <div class="card">
            <div class="thumb-pair">
                <div class="thumb"><img src="${g.oldest.url}"><span class="tag">oldest</span></div>
                <div class="thumb"><img src="${g.newest.url}"><span class="tag">newest</span></div>
            </div>
            <div class="meta">
                <strong>${escapeHtml(g.camera_id)}</strong> · track #${g.track_id} · ${g.crop_count} crop(s)
            </div>
            <div class="card-actions">
                <select data-session="${escapeHtml(g.session_key)}">
                    ${personSelectOptions(null)}
                </select>
                <button data-confirm="${escapeHtml(g.session_key)}">Save</button>
                <button class="danger" data-delete-group="${escapeHtml(g.session_key)}">Delete</button>
            </div>
        </div>
    `).join("");

    el.querySelectorAll("button[data-confirm]").forEach((btn) => {
        btn.addEventListener("click", () => onAssignClick(btn.dataset.confirm));
    });

    el.querySelectorAll("button[data-delete-group]").forEach((btn) => {
        btn.addEventListener("click", () => onDeleteGroup(btn.dataset.deleteGroup));
    });
}

function renderPeople(people) {
    const el = document.getElementById("people");
    const emptyEl = document.getElementById("people-empty");

    if (people.length === 0) {
        el.innerHTML = "";
        emptyEl.hidden = false;
        return;
    }
    emptyEl.hidden = true;

    el.innerHTML = people.map((p) => `
        <div class="card">
            <div class="thumb-pair">
                ${p.oldest ? `<div class="thumb"><img src="${p.oldest.url}"><span class="tag">oldest</span></div>` : ""}
                ${p.newest ? `<div class="thumb"><img src="${p.newest.url}"><span class="tag">newest</span></div>` : ""}
            </div>
            <div class="meta"><strong>${escapeHtml(p.name)}</strong> · ${p.crop_count} crop(s)</div>
            <div class="card-actions">
                <select data-merge-target="${p.id}">
                    <option value="">Merge into...</option>
                    ${personSelectOptions(p.id)}
                </select>
                <button data-merge-go="${p.id}">Merge</button>
                <button class="danger" data-delete="${p.id}" data-name="${escapeHtml(p.name)}">Delete</button>
            </div>
        </div>
    `).join("");

    el.querySelectorAll("button[data-merge-go]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const fromId = parseInt(btn.dataset.mergeGo, 10);
            const select = el.querySelector(`select[data-merge-target="${fromId}"]`);
            const intoId = parseInt(select.value, 10);
            if (!select.value) {
                alert("Pick a person to merge into first.");
                return;
            }
            onMergeClick(fromId, intoId);
        });
    });

    el.querySelectorAll("button[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => onDeletePerson(
            parseInt(btn.dataset.delete, 10), btn.dataset.name
        ));
    });
}

async function onAssignClick(sessionKey) {
    const select = document.querySelector(`select[data-session="${sessionKey}"]`);
    const value = select.value;

    if (!value) {
        const name = prompt("Name for this new person:");
        if (!name || !name.trim()) return;
        await api("/api/people/assign", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_key: sessionKey, name: name.trim() }),
        });
        await loadAll();
        return;
    }

    // Assigning to an existing person is a merge - review crops from both
    // sides first so outliers can be dropped before it's final.
    const personId = parseInt(value, 10);
    const groupCrops = await api(`/api/people/group/${encodeURIComponent(sessionKey)}/crops`);
    const personCrops = await api(`/api/people/${personId}/crops`);
    const person = peopleCache.find((p) => p.id === personId);

    openMergeModal({
        title: `Merge into "${person ? person.name : personId}"`,
        sub: "Review crops from both sides. Click a crop to exclude it before merging.",
        crops: [...groupCrops.crops, ...personCrops.crops],
        onConfirm: async (excludedIds) => {
            await api("/api/people/assign", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_key: sessionKey,
                    person_id: personId,
                    exclude_crop_ids: excludedIds,
                }),
            });
        },
    });
}

async function onMergeClick(fromId, intoId) {
    const fromPerson = peopleCache.find((p) => p.id === fromId);
    const intoPerson = peopleCache.find((p) => p.id === intoId);

    const [fromCrops, intoCrops] = await Promise.all([
        api(`/api/people/${fromId}/crops`),
        api(`/api/people/${intoId}/crops`),
    ]);

    openMergeModal({
        title: `Merge "${fromPerson.name}" into "${intoPerson.name}"`,
        sub: "Review crops from both people. Click a crop to exclude it before merging.",
        crops: [...fromCrops.crops, ...intoCrops.crops],
        onConfirm: async (excludedIds) => {
            await api("/api/people/merge", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ from_id: fromId, into_id: intoId, exclude_crop_ids: excludedIds }),
            });
        },
    });
}

function openMergeModal({ title, sub, crops, onConfirm }) {
    const excluded = new Set();

    document.getElementById("modal-title").innerText = title;
    document.getElementById("modal-sub").innerText = sub;

    const cropsEl = document.getElementById("modal-crops");
    cropsEl.innerHTML = crops.map((c) => `
        <div class="crop-cell" data-crop-id="${c.id}">
            <img src="${c.url}">
            <button class="x" data-toggle-crop="${c.id}" title="Exclude this crop">×</button>
        </div>
    `).join("");

    cropsEl.querySelectorAll("[data-toggle-crop]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const id = parseInt(btn.dataset.toggleCrop, 10);
            const cell = btn.closest(".crop-cell");
            if (excluded.has(id)) {
                excluded.delete(id);
                cell.classList.remove("excluded");
            } else {
                excluded.add(id);
                cell.classList.add("excluded");
            }
        });
    });

    const backdrop = document.getElementById("modal-backdrop");
    backdrop.hidden = false;

    const confirmBtn = document.getElementById("modal-confirm");
    const cancelBtn = document.getElementById("modal-cancel");

    const close = () => { backdrop.hidden = true; };

    const confirmHandler = async () => {
        confirmBtn.disabled = true;
        try {
            await onConfirm(Array.from(excluded));
            close();
            await loadAll();
        } catch (e) {
            alert(e.message);
        } finally {
            confirmBtn.disabled = false;
        }
    };

    confirmBtn.onclick = confirmHandler;
    cancelBtn.onclick = close;
}

async function onDeleteGroup(sessionKey) {
    if (!confirm("Discard this whole track and its crops? This can't be undone.")) return;
    await api(`/api/people/group/${encodeURIComponent(sessionKey)}`, { method: "DELETE" });
    await loadAll();
}

async function onDeletePerson(id, name) {
    if (!confirm(`Delete "${name}" and all their crops? This can't be undone.`)) return;
    await api(`/api/people/${id}`, { method: "DELETE" });
    await loadAll();
}

loadAll();
setInterval(loadAll, 15000);

</script>

</body>
</html>
"""


def render_people_html():
    return PEOPLE_HTML
