"""
/people page: review unlabeled ByteTrack sightings (crops the capture node
shipped in but nobody's named yet), name them or merge them into an
existing person, review/delete outlier crops, and manage named people
(including opening a person's full gallery to mass-select crops that were
wrongly merged in and move or delete them). Plain HTML/CSS/vanilla JS,
served by streaming/relay_server.py.
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
.toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    font-size: 12px;
    color: #999;
}
.toolbar label {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
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
    position: relative;
}
.card.selected { border-color: #2a7; }
.card-select {
    position: absolute;
    top: 10px;
    right: 10px;
    width: 18px;
    height: 18px;
    cursor: pointer;
    z-index: 2;
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
    cursor: zoom-in;
}
.thumb .tag {
    position: absolute;
    bottom: 4px;
    left: 4px;
    font-size: 10px;
    background: rgba(0,0,0,0.7);
    padding: 1px 5px;
    border-radius: 3px;
    pointer-events: none;
}
.card .meta {
    font-size: 12px;
    color: #999;
    margin-bottom: 8px;
}
.card .meta strong { color: #ddd; }
.card .meta a.view-all {
    color: #6cf;
    text-decoration: none;
    margin-left: 6px;
}
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
button:disabled { opacity: 0.4; cursor: not-allowed; }
button.danger { background: #3a1a1a; border-color: #663; color: #f88; }
button.danger:hover { background: #522; }
.card-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
}
.card-actions select { flex: 1; min-width: 110px; }

/* ---- Modal (merge review / person gallery) ---- */
.modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.7);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    z-index: 10;
}
.modal-backdrop[hidden] { display: none; }
.modal-box {
    background: #1a1a1a;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 20px;
    width: 100%;
    max-width: 760px;
    max-height: 85vh;
    overflow-y: auto;
}
.modal-box h3 { margin: 0 0 4px; font-size: 16px; }
.modal-box .sub { color: #888; font-size: 12px; margin-bottom: 14px; }
.crop-section { margin-bottom: 18px; }
.crop-section h4 {
    font-size: 12px;
    color: #999;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin: 0 0 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid #2a2a2a;
}
.crop-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(90px, 1fr));
    gap: 8px;
}
.crop-cell {
    position: relative;
    -webkit-touch-callout: none;
    -webkit-user-select: none;
    user-select: none;
}
.crop-cell img {
    -webkit-user-drag: none;
    width: 100%;
    height: 90px;
    object-fit: cover;
    border-radius: 4px;
    border: 2px solid transparent;
    cursor: zoom-in;
    display: block;
}
.crop-cell.excluded img { opacity: 0.3; border-color: #a44; }
.crop-cell.checked img { border-color: #2a7; }
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
    padding: 0;
}
.crop-cell .pick {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    cursor: pointer;
}
.modal-box .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 4px;
}
.gallery-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 14px;
    padding-bottom: 14px;
    border-bottom: 1px solid #2a2a2a;
    font-size: 12px;
    color: #999;
}
.gallery-toolbar label {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
}
.gallery-toolbar select { min-width: 140px; }

/* ---- Lightbox ---- */
#lightbox-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.9);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    cursor: zoom-out;
    padding: 24px;
}
#lightbox-backdrop[hidden] { display: none; }
#lightbox-backdrop img {
    max-width: 100%;
    max-height: 100%;
    border-radius: 6px;
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
        <div class="toolbar">
            <label><input type="checkbox" id="select-all-unassigned"> Select all</label>
            <span id="unassigned-selected-count">0 selected</span>
            <button class="danger" id="bulk-delete-unassigned" disabled>Delete selected</button>
        </div>
        <div id="unassigned" class="card-grid"></div>
        <div id="unassigned-empty" class="empty" hidden>No unlabeled sightings right now.</div>
    </section>

    <section>
        <h2>Known people</h2>
        <div id="people" class="card-grid"></div>
        <div id="people-empty" class="empty" hidden>No named people yet.</div>
    </section>

</div>

<!-- Merge review modal -->
<div class="modal-backdrop" id="modal-backdrop" hidden>
    <div class="modal-box">
        <h3 id="modal-title"></h3>
        <div class="sub" id="modal-sub"></div>
        <div class="crop-section">
            <h4 id="modal-existing-heading"></h4>
            <div class="crop-grid" id="modal-existing-crops"></div>
        </div>
        <div class="crop-section">
            <h4 id="modal-new-heading"></h4>
            <div class="crop-grid" id="modal-new-crops"></div>
        </div>
        <div class="actions">
            <button id="modal-cancel">Cancel</button>
            <button id="modal-confirm">Confirm merge</button>
        </div>
    </div>
</div>

<!-- Person gallery modal (mass select -> delete or move) -->
<div class="modal-backdrop" id="gallery-backdrop" hidden>
    <div class="modal-box">
        <h3 id="gallery-title"></h3>
        <div class="sub">Click a crop to enlarge it. Check crops to delete or move them to another person - useful when two different people ended up under one name.</div>
        <div class="gallery-toolbar">
            <label><input type="checkbox" id="gallery-select-all"> Select all</label>
            <span id="gallery-selected-count">0 selected</span>
            <button class="danger" id="gallery-delete-selected" disabled>Delete selected</button>
            <select id="gallery-move-target"></select>
            <button id="gallery-move-selected" disabled>Move selected</button>
        </div>
        <div class="crop-grid" id="gallery-crops"></div>
        <div class="actions">
            <button id="gallery-close">Close</button>
        </div>
    </div>
</div>

<!-- Lightbox -->
<div id="lightbox-backdrop" hidden>
    <img id="lightbox-img" src="">
</div>

<script>

let peopleCache = [];
let unassignedCache = [];
let selectedUnassigned = new Set();
let gallerySelected = new Set();
let galleryPersonId = null;
let galleryCrops = [];

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

function openLightbox(url) {
    document.getElementById("lightbox-img").src = url;
    document.getElementById("lightbox-backdrop").hidden = false;
}

document.getElementById("lightbox-backdrop").addEventListener("click", () => {
    document.getElementById("lightbox-backdrop").hidden = true;
});

let suppressLightboxUntil = 0;

function bindLightbox(root) {
    root.querySelectorAll("img[data-zoom]").forEach((img) => {
        img.draggable = false;
        img.addEventListener("click", (e) => {
            e.stopPropagation();
            // a drag-select just finished - don't also open the lightbox
            if (Date.now() < suppressLightboxUntil) return;
            openLightbox(img.dataset.zoom);
        });
    });
}

// Gallery-style drag select. Touch: long-press a photo (~350ms), then drag
// over others - the first photo's state decides whether the drag selects or
// deselects. Mouse: press on a photo and drag onto another one. Quick taps
// still open the lightbox, and scrolling still works (a long-press that
// hasn't fired yet is cancelled if the finger moves).
//   onSet(id, on) is called once per photo touched by the drag.
//   isOn(id) tells whether a photo is currently selected.
function enableDragSelect(container, isOnFn, onSetFn) {
    // Grids re-render into the same container element - bind listeners once
    // and just swap in the latest callbacks on later calls.
    if (container._dragSel) {
        container._dragSel.isOn = isOnFn;
        container._dragSel.onSet = onSetFn;
        return;
    }
    const cb = container._dragSel = { isOn: isOnFn, onSet: onSetFn };
    const isOn = (id) => cb.isOn(id);
    const onSet = (id, on) => cb.onSet(id, on);

    const LONG_PRESS_MS = 350;
    const MOVE_TOLERANCE = 10;

    let timer = null;
    let active = false;
    let mode = true;
    let visited = new Set();
    let startX = 0, startY = 0;
    let startCell = null;
    let mouseDown = false;

    const idOf = (cell) => parseInt(cell.dataset.cropId, 10);

    const cellAt = (x, y) => {
        const el = document.elementFromPoint(x, y);
        const cell = el && el.closest ? el.closest(".crop-cell") : null;
        return cell && container.contains(cell) ? cell : null;
    };

    const touch = (cell) => {
        if (!cell) return;
        const id = idOf(cell);
        if (visited.has(id)) return;
        visited.add(id);
        onSet(id, mode);
    };

    const begin = (cell) => {
        active = true;
        visited = new Set();
        mode = !isOn(idOf(cell));
        touch(cell);
    };

    const finish = () => {
        clearTimeout(timer);
        timer = null;
        if (active) suppressLightboxUntil = Date.now() + 400;
        active = false;
        mouseDown = false;
        startCell = null;
    };

    // ---- touch ----
    container.addEventListener("touchstart", (e) => {
        if (e.touches.length !== 1) return;
        const cell = e.target.closest ? e.target.closest(".crop-cell") : null;
        if (!cell) return;
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        startCell = cell;
        timer = setTimeout(() => {
            begin(startCell);
            if (navigator.vibrate) navigator.vibrate(20);
        }, LONG_PRESS_MS);
    }, { passive: true });

    container.addEventListener("touchmove", (e) => {
        const t = e.touches[0];
        if (!active) {
            if (Math.abs(t.clientX - startX) > MOVE_TOLERANCE || Math.abs(t.clientY - startY) > MOVE_TOLERANCE) {
                clearTimeout(timer);
                timer = null;
            }
            return;
        }
        e.preventDefault(); // stop the page scrolling while dragging a selection
        touch(cellAt(t.clientX, t.clientY));
    }, { passive: false });

    container.addEventListener("touchend", finish);
    container.addEventListener("touchcancel", finish);

    // Android long-press on an image opens a context menu - suppress it
    container.addEventListener("contextmenu", (e) => {
        if (e.target.closest && e.target.closest(".crop-cell")) e.preventDefault();
    });

    // ---- mouse ----
    container.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        if (e.target.closest && e.target.closest("button, input")) return;
        const cell = e.target.closest ? e.target.closest(".crop-cell") : null;
        if (!cell) return;
        mouseDown = true;
        startCell = cell;
    });

    container.addEventListener("mousemove", (e) => {
        if (!mouseDown) return;
        const cell = cellAt(e.clientX, e.clientY);
        if (!cell) return;
        if (!active) {
            if (cell === startCell) return;
            begin(startCell);
        }
        touch(cell);
    });

    document.addEventListener("mouseup", finish);
}

async function loadAll() {
    const [unassignedData, peopleData] = await Promise.all([
        api("/api/people/unassigned"),
        api("/api/people"),
    ]);
    peopleCache = peopleData.people;
    unassignedCache = unassignedData.groups;

    // drop selections for groups that no longer exist
    const stillThere = new Set(unassignedCache.map((g) => g.session_key));
    selectedUnassigned = new Set([...selectedUnassigned].filter((k) => stillThere.has(k)));

    renderUnassigned();
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

// ---------------- Unassigned sightings ----------------

function renderUnassigned() {
    const el = document.getElementById("unassigned");
    const emptyEl = document.getElementById("unassigned-empty");
    const groups = unassignedCache;

    if (groups.length === 0) {
        el.innerHTML = "";
        emptyEl.hidden = false;
        updateUnassignedToolbar();
        return;
    }
    emptyEl.hidden = true;

    el.innerHTML = groups.map((g) => `
        <div class="card ${selectedUnassigned.has(g.session_key) ? "selected" : ""}" data-card-session="${escapeHtml(g.session_key)}">
            <input type="checkbox" class="card-select" data-select-session="${escapeHtml(g.session_key)}" ${selectedUnassigned.has(g.session_key) ? "checked" : ""}>
            <div class="thumb-pair">
                <div class="thumb"><img data-zoom="${g.oldest.url}" src="${g.oldest.url}"><span class="tag">oldest</span></div>
                <div class="thumb"><img data-zoom="${g.newest.url}" src="${g.newest.url}"><span class="tag">newest</span></div>
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

    bindLightbox(el);

    el.querySelectorAll("button[data-confirm]").forEach((btn) => {
        btn.addEventListener("click", () => onAssignClick(btn.dataset.confirm));
    });

    el.querySelectorAll("button[data-delete-group]").forEach((btn) => {
        btn.addEventListener("click", () => onDeleteGroup(btn.dataset.deleteGroup));
    });

    el.querySelectorAll("input[data-select-session]").forEach((cb) => {
        cb.addEventListener("change", () => {
            const key = cb.dataset.selectSession;
            if (cb.checked) selectedUnassigned.add(key);
            else selectedUnassigned.delete(key);
            cb.closest(".card").classList.toggle("selected", cb.checked);
            updateUnassignedToolbar();
        });
    });

    updateUnassignedToolbar();
}

function updateUnassignedToolbar() {
    const count = selectedUnassigned.size;
    document.getElementById("unassigned-selected-count").innerText = count + " selected";
    document.getElementById("bulk-delete-unassigned").disabled = count === 0;

    const allSelected = unassignedCache.length > 0 && count === unassignedCache.length;
    document.getElementById("select-all-unassigned").checked = allSelected;
}

document.getElementById("select-all-unassigned").addEventListener("change", (e) => {
    selectedUnassigned = e.target.checked ? new Set(unassignedCache.map((g) => g.session_key)) : new Set();
    renderUnassigned();
});

document.getElementById("bulk-delete-unassigned").addEventListener("click", async () => {
    const keys = [...selectedUnassigned];
    if (keys.length === 0) return;
    if (!confirm(`Discard ${keys.length} selected sighting(s) and all their crops? This can't be undone.`)) return;

    await api("/api/people/unassigned/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_keys: keys }),
    });
    selectedUnassigned.clear();
    await loadAll();
});

// ---------------- Known people ----------------

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
                ${p.oldest ? `<div class="thumb"><img data-zoom="${p.oldest.url}" src="${p.oldest.url}"><span class="tag">oldest</span></div>` : ""}
                ${p.newest ? `<div class="thumb"><img data-zoom="${p.newest.url}" src="${p.newest.url}"><span class="tag">newest</span></div>` : ""}
            </div>
            <div class="meta">
                <strong>${escapeHtml(p.name)}</strong> · ${p.crop_count} crop(s)
                <a href="#" class="view-all" data-view-gallery="${p.id}">view all →</a>
            </div>
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

    bindLightbox(el);

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

    el.querySelectorAll("a[data-view-gallery]").forEach((a) => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            openGallery(parseInt(a.dataset.viewGallery, 10));
        });
    });
}

// ---------------- Assign / merge (two-table review) ----------------

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

    const personId = parseInt(value, 10);
    const groupCrops = await api(`/api/people/group/${encodeURIComponent(sessionKey)}/crops`);
    const personCrops = await api(`/api/people/${personId}/crops`);
    const person = peopleCache.find((p) => p.id === personId);
    const name = person ? person.name : personId;

    openMergeModal({
        title: `Merge into "${name}"`,
        sub: "Review crops from both sides. Click a crop to exclude it before merging; click to enlarge.",
        existingHeading: `Already "${name}" (${personCrops.crops.length})`,
        existingCrops: personCrops.crops,
        newHeading: `New crops being added (${groupCrops.crops.length})`,
        newCrops: groupCrops.crops,
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
        sub: "Review crops from both people. Click a crop to exclude it before merging; click to enlarge.",
        existingHeading: `Already "${intoPerson.name}" (${intoCrops.crops.length})`,
        existingCrops: intoCrops.crops,
        newHeading: `Being merged in from "${fromPerson.name}" (${fromCrops.crops.length})`,
        newCrops: fromCrops.crops,
        onConfirm: async (excludedIds) => {
            await api("/api/people/merge", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ from_id: fromId, into_id: intoId, exclude_crop_ids: excludedIds }),
            });
        },
    });
}

function renderCropGrid(container, crops, excluded) {
    container.innerHTML = crops.map((c) => `
        <div class="crop-cell" data-crop-id="${c.id}">
            <img data-zoom="${c.url}" src="${c.url}">
            <button class="x" data-toggle-crop="${c.id}" title="Exclude this crop">×</button>
        </div>
    `).join("");

    bindLightbox(container);

    container.querySelectorAll("[data-toggle-crop]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
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

    // long-press + drag across photos to exclude (or un-exclude) several at once
    enableDragSelect(
        container,
        (id) => excluded.has(id),
        (id, on) => {
            if (on) excluded.add(id); else excluded.delete(id);
            const cell = container.querySelector(`.crop-cell[data-crop-id="${id}"]`);
            if (cell) cell.classList.toggle("excluded", on);
        }
    );
}

function openMergeModal({ title, sub, existingHeading, existingCrops, newHeading, newCrops, onConfirm }) {
    const excluded = new Set();

    document.getElementById("modal-title").innerText = title;
    document.getElementById("modal-sub").innerText = sub;
    document.getElementById("modal-existing-heading").innerText = existingHeading;
    document.getElementById("modal-new-heading").innerText = newHeading;

    renderCropGrid(document.getElementById("modal-existing-crops"), existingCrops, excluded);
    renderCropGrid(document.getElementById("modal-new-crops"), newCrops, excluded);

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

// ---------------- Person gallery (mass select -> delete/move) ----------------

async function openGallery(personId) {
    const person = peopleCache.find((p) => p.id === personId);
    galleryPersonId = personId;
    gallerySelected = new Set();

    const data = await api(`/api/people/${personId}/crops`);
    galleryCrops = data.crops;

    document.getElementById("gallery-title").innerText = `${person ? person.name : personId} — all crops`;

    populateMoveTargets();
    renderGallery();
    document.getElementById("gallery-backdrop").hidden = false;
}

// Rebuilt after every move too, so a person created via "+ New person..."
// shows up as a target without having to reopen the gallery.
function populateMoveTargets() {
    const moveTarget = document.getElementById("gallery-move-target");
    moveTarget.innerHTML = '<option value="">Move to...</option>' +
        '<option value="__new__">+ New person...</option>' +
        peopleCache.filter((p) => p.id !== galleryPersonId)
            .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
}

function renderGallery() {
    const el = document.getElementById("gallery-crops");
    el.innerHTML = galleryCrops.map((c) => `
        <div class="crop-cell ${gallerySelected.has(c.id) ? "checked" : ""}" data-crop-id="${c.id}">
            <input type="checkbox" class="pick" data-pick="${c.id}" ${gallerySelected.has(c.id) ? "checked" : ""}>
            <img data-zoom="${c.url}" src="${c.url}">
        </div>
    `).join("");

    bindLightbox(el);

    el.querySelectorAll("input[data-pick]").forEach((cb) => {
        cb.addEventListener("change", (e) => {
            e.stopPropagation();
            const id = parseInt(cb.dataset.pick, 10);
            if (cb.checked) gallerySelected.add(id);
            else gallerySelected.delete(id);
            cb.closest(".crop-cell").classList.toggle("checked", cb.checked);
            updateGalleryToolbar();
        });
    });

    // long-press + drag across photos to select (or deselect) several at once
    enableDragSelect(
        el,
        (id) => gallerySelected.has(id),
        (id, on) => {
            if (on) gallerySelected.add(id); else gallerySelected.delete(id);
            const cell = el.querySelector(`.crop-cell[data-crop-id="${id}"]`);
            if (cell) {
                cell.classList.toggle("checked", on);
                const cb = cell.querySelector("input[data-pick]");
                if (cb) cb.checked = on;
            }
            updateGalleryToolbar();
        }
    );

    updateGalleryToolbar();
}

function updateGalleryToolbar() {
    const count = gallerySelected.size;
    document.getElementById("gallery-selected-count").innerText = count + " selected";
    document.getElementById("gallery-delete-selected").disabled = count === 0;
    document.getElementById("gallery-move-selected").disabled = count === 0 || !document.getElementById("gallery-move-target").value;

    const allSelected = galleryCrops.length > 0 && count === galleryCrops.length;
    document.getElementById("gallery-select-all").checked = allSelected;
}

document.getElementById("gallery-select-all").addEventListener("change", (e) => {
    gallerySelected = e.target.checked ? new Set(galleryCrops.map((c) => c.id)) : new Set();
    renderGallery();
});

document.getElementById("gallery-move-target").addEventListener("change", updateGalleryToolbar);

document.getElementById("gallery-delete-selected").addEventListener("click", async () => {
    const ids = [...gallerySelected];
    if (ids.length === 0) return;
    if (!confirm(`Delete ${ids.length} selected crop(s)? This can't be undone.`)) return;

    await api("/api/crops/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ crop_ids: ids }),
    });

    galleryCrops = galleryCrops.filter((c) => !gallerySelected.has(c.id));
    gallerySelected.clear();
    renderGallery();
    await loadAll();
});

document.getElementById("gallery-move-selected").addEventListener("click", async () => {
    const ids = [...gallerySelected];
    const targetValue = document.getElementById("gallery-move-target").value;
    if (ids.length === 0 || !targetValue) return;

    let body;
    if (targetValue === "__new__") {
        // Two different people ended up under one name - split them out into
        // a brand new person without leaving the gallery.
        const name = prompt(`Name for the new person these ${ids.length} crop(s) belong to:`);
        if (!name || !name.trim()) return;
        body = { crop_ids: ids, new_person_name: name.trim() };
    } else {
        const toId = parseInt(targetValue, 10);
        const toName = peopleCache.find((p) => p.id === toId)?.name || toId;
        if (!confirm(`Move ${ids.length} selected crop(s) to "${toName}"?`)) return;
        body = { crop_ids: ids, to_person_id: toId };
    }

    await api("/api/crops/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });

    galleryCrops = galleryCrops.filter((c) => !gallerySelected.has(c.id));
    gallerySelected.clear();
    renderGallery();
    await loadAll();
    populateMoveTargets();
});

document.getElementById("gallery-close").addEventListener("click", () => {
    document.getElementById("gallery-backdrop").hidden = true;
});

loadAll();
setInterval(loadAll, 15000);

</script>

</body>
</html>
"""


def render_people_html():
    return PEOPLE_HTML
