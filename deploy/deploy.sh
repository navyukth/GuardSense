#!/usr/bin/env bash
#
# Deploys this checkout to the two service folders the containers are built
# from on the Pi5:
#
#   ~/npm/guardsense-relay     (WebRTC relay + web UI + SQLite identity store)
#   ~/npm/guardsense-capture   (cameras + YOLO/ByteTrack/OSNet pipeline)
#
# Only services whose files actually changed get rebuilt, so a UI-only change
# doesn't restart the capture node (or vice versa). Never touches .env files,
# data/ or the yolov8n.pt weights - those live only on the Pi.
#
#   bash deploy/deploy.sh            # deploy what changed
#   FORCE=1 bash deploy/deploy.sh    # rebuild both regardless
#   DRY_RUN=1 bash deploy/deploy.sh  # show what would change, touch nothing
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NPM_DIR="${NPM_DIR:-$HOME/npm}"
RELAY_DIR="$NPM_DIR/guardsense-relay"
CAPTURE_DIR="$NPM_DIR/guardsense-capture"
FORCE="${FORCE:-0}"
DRY_RUN="${DRY_RUN:-0}"

# streaming/ modules that belong to the capture image; every other .py there
# is relay code. New UI modules therefore deploy without editing this script.
CAPTURE_MODULES=(__init__ capture_node identity_matcher export_ncnn)

relay_changed=0
capture_changed=0

log() { echo "[deploy] $*"; }

# copy_if_different SRC DST -> returns 0 (and copies) only if DST differs
copy_if_different() {
    local src="$1" dst="$2"
    if cmp -s "$src" "$dst" 2>/dev/null; then
        return 1
    fi
    log "  changed: ${dst#$NPM_DIR/}"
    if [ "$DRY_RUN" != "1" ]; then
        mkdir -p "$(dirname "$dst")"
        cp "$src" "$dst"
    fi
    return 0
}

# sync_dir SRC/ DST/ -> returns 0 if anything differed
sync_dir() {
    local src="$1" dst="$2" out
    mkdir -p "$dst"
    out="$(rsync -rc --delete --itemize-changes --exclude '__pycache__' --exclude '*.pyc' \
        $([ "$DRY_RUN" = "1" ] && echo --dry-run) "$src" "$dst")"
    if [ -n "$out" ]; then
        echo "$out" | sed "s|^|[deploy]   |"
        return 0
    fi
    return 1
}

is_capture_module() {
    local name="$1" m
    for m in "${CAPTURE_MODULES[@]}"; do
        [ "$m" = "$name" ] && return 0
    done
    return 1
}

for dir in "$RELAY_DIR" "$CAPTURE_DIR"; do
    if [ ! -d "$dir" ]; then
        echo "[deploy] ERROR: $dir doesn't exist - create the service folder and its .env first" >&2
        exit 1
    fi
done

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/relay/streaming" "$STAGE/capture/streaming"

for f in "$REPO_DIR"/streaming/*.py; do
    name="$(basename "$f" .py)"
    if is_capture_module "$name"; then
        cp "$f" "$STAGE/capture/streaming/"
    else
        cp "$f" "$STAGE/relay/streaming/"
    fi
done
# the relay needs the package marker too
cp "$REPO_DIR/streaming/__init__.py" "$STAGE/relay/streaming/"

log "commit: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# ---------------- relay ----------------
log "relay:"
sync_dir "$STAGE/relay/streaming/" "$RELAY_DIR/streaming/" && relay_changed=1 || true
copy_if_different "$REPO_DIR/streaming/relay.Dockerfile"        "$RELAY_DIR/relay.Dockerfile"    && relay_changed=1 || true
copy_if_different "$REPO_DIR/streaming/relay-docker-compose.yml" "$RELAY_DIR/docker-compose.yml"  && relay_changed=1 || true
copy_if_different "$REPO_DIR/streaming/relay-requirements.txt"   "$RELAY_DIR/requirements.txt"    && relay_changed=1 || true

# ---------------- capture ----------------
log "capture:"
sync_dir "$STAGE/capture/streaming/" "$CAPTURE_DIR/streaming/" && capture_changed=1 || true
for pkg in camera detection tracking embedding DataClass; do
    sync_dir "$REPO_DIR/$pkg/" "$CAPTURE_DIR/$pkg/" && capture_changed=1 || true
done
copy_if_different "$REPO_DIR/streaming/capture.Dockerfile"         "$CAPTURE_DIR/capture.Dockerfile"  && capture_changed=1 || true
copy_if_different "$REPO_DIR/streaming/capture-docker-compose.yml" "$CAPTURE_DIR/docker-compose.yml" && capture_changed=1 || true
copy_if_different "$REPO_DIR/streaming/capture-requirements.txt"   "$CAPTURE_DIR/requirements.txt"   && capture_changed=1 || true

if [ "$FORCE" = "1" ]; then
    relay_changed=1
    capture_changed=1
    log "FORCE=1 - rebuilding both services"
fi

if [ "$DRY_RUN" = "1" ]; then
    log "dry run - relay_changed=$relay_changed capture_changed=$capture_changed, nothing was modified"
    exit 0
fi

if [ "$relay_changed" = "0" ] && [ "$capture_changed" = "0" ]; then
    log "nothing changed - nothing to deploy"
    exit 0
fi

# ---------------- rebuild ----------------
if [ "$relay_changed" = "1" ]; then
    log "rebuilding relay..."
    (cd "$RELAY_DIR" && docker compose up -d --build)
fi

if [ "$capture_changed" = "1" ]; then
    log "rebuilding capture (slow if requirements/Dockerfile changed: torch reinstalls)..."
    (cd "$CAPTURE_DIR" && docker compose up -d --build)
fi

# ---------------- health checks ----------------
fail=0

if [ "$relay_changed" = "1" ]; then
    log "waiting for relay to answer..."
    ok=0
    for _ in $(seq 1 30); do
        if curl -sf -o /dev/null http://localhost:8080/login; then ok=1; break; fi
        sleep 3
    done
    if [ "$ok" = "1" ]; then
        log "relay: healthy"
    else
        log "relay: NOT answering on :8080"
        docker logs --tail 30 guardsense-relay 2>&1 || true
        fail=1
    fi
fi

if [ "$capture_changed" = "1" ]; then
    log "checking capture stays up (it needs ~20s to load models)..."
    sleep 30
    state="$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' guardsense-capture 2>/dev/null || echo 'false 0')"
    log "capture: running/restarts = $state"
    if [ "${state%% *}" != "true" ]; then
        log "capture: NOT running"
        docker logs --tail 30 guardsense-capture 2>&1 || true
        fail=1
    fi
fi

if [ "$fail" != "0" ]; then
    log "DEPLOY FAILED - to roll back, git revert the bad commit and push (or re-run an older commit's workflow)"
    exit 1
fi

log "deploy OK"
