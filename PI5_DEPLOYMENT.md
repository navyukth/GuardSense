# Pi5 Deployment Reference

How GuardSense's two services are actually deployed on the Raspberry Pi5
that hosts them. This is the sanitized, GuardSense-only version of the
infra notes - no real credentials, no unrelated services running on the
same box (this Pi5 also happens to host a couple of personal projects
that have nothing to do with GuardSense; they're omitted here).

For real credentials, see `serverpi.md` (gitignored, not in this repo -
ask whoever runs the Pi5 for it, or reconstruct from your own `.env`).

---

## 1. Network Overview

```
Internet
    |
Domain: your-domain.tld (DNS pointed at your static public IP)
    |
Static Public IP
    | Router port forwards (Section 2)
Router (any brand supporting port forwarding)
    |
Raspberry Pi 5 - LAN IP e.g. 192.168.1.50 (DHCP reservation recommended)
    |
    +-- Docker bridge network: npm_network
    |     +-- nginx-proxy-manager (container: npm) - ports 80, 443, 81(admin)
    |
    +-- Docker host-networked containers (network_mode: host)
          +-- coturn              - STUN/TURN, ports 3478, 5349, 49152-49352
          +-- guardsense-relay    - WebRTC relay + web UI, port 8080
          +-- guardsense-capture  - camera capture + inference pipeline
              (no exposed port - pushes to guardsense-relay over its
               ingest websocket, doesn't serve anything itself)
```

| Item | Notes |
|---|---|
| UFW (firewall) | Keep disabled, or explicitly allow forwarded/external traffic - a naive UFW enable will silently break every port-forwarded service |
| Hairpin NAT | Most consumer routers don't support it - testing a public domain from a device on the same LAN as the Pi needs `curl --resolve <domain>:443:<pi-lan-ip>`, or the request just hangs |

## 2. Router Port Forwards

| Name | Protocol | Private IP:Port | Public Port |
|---|---|---|---|
| HTTP | TCP | `<pi-ip>:80` | 80 |
| HTTPS | TCP | `<pi-ip>:443` | 443 |
| STUN-TURN-UDP | UDP | `<pi-ip>:3478` | 3478 |
| STUN-TURN-TCP | TCP | `<pi-ip>:3478` | 3478 |
| TURN-RELAY | UDP | `<pi-ip>:49152-49352` | 49152-49352 |
| TURNS-TLS | TCP/UDP | `<pi-ip>:5349` | 5349 |

`guard.your-domain.tld` (guardsense-relay's web UI) does **not** need its
own port forward - it's reached through nginx on 443 like any other
HTTPS subdomain. Only coturn's raw UDP/TCP ports need direct forwarding.

## 3. Nginx Proxy Manager

- Admin UI: `http://<pi-lan-ip>:81` (LAN only)
- Proxy host for `guard.your-domain.tld`:
  - Forward Hostname/IP: **`<pi-lan-ip>`** (the raw IP, not the container
    name) - `guardsense-relay` runs with `network_mode: host`, so it
    never joins the `npm_network` bridge and has no container-name DNS
    entry there
  - Forward Port: `8080`
  - Websockets: **on** (the ingest websocket and the live-feed WebRTC
    signaling both need it)
  - SSL Certificate: Let's Encrypt, Force SSL on, HTTP/2 on

**Rule of thumb:** anything on `network_mode: host` (coturn,
guardsense-relay, guardsense-capture) gets reached by the Pi's raw LAN
IP in nginx, never by container name - only bridge-network containers
get that.

## 4. coturn (STUN/TURN)

`network_mode: host` is required so STUN can correctly reflect the Pi's
real public IP/port back to clients - bridge NAT breaks this.

`~/npm/coturn/turnserver.conf`:
```
listening-port=3478
tls-listening-port=5349
min-port=49152
max-port=49352

external-ip=<your-static-public-ip>
realm=your-domain.tld
server-name=your-domain.tld

user=guardsense:<a-real-password>

fingerprint
lt-cred-mech

no-multicast-peers
no-cli
```

Matches `TURN_USERNAME`/`TURN_CREDENTIAL`/`TURN_HOST` in `.env`.

**Testing STUN reachability** - must be done from *outside* the LAN
(mobile hotspot; same-network tests give false results from hairpin
NAT): https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/
Enter `stun:<your-domain-or-ip>:3478`, click Add Server -> Gather
candidates, look for `srflx` candidates with your public IP.

## 5. GuardSense services

```
~/npm/
├── docker-compose.yml       # npm + coturn
├── coturn/
│   └── turnserver.conf
├── guardsense-relay/
│   ├── Dockerfile / relay.Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   ├── .env                 # ADMIN_PASSWORD, TURN_*, RELAY_INGEST_TOKEN, CAMERA_IDS
│   ├── data/                # identities.db + crops_relay/ (volume-mounted, persists across rebuilds)
│   └── streaming/
└── guardsense-capture/
    ├── Dockerfile / capture.Dockerfile
    ├── docker-compose.yml
    ├── requirements.txt
    ├── .env                 # RTSP_*, RELAY_URL, RELAY_INGEST_TOKEN, YOLO_MODEL, ...
    ├── yolov8n.pt
    ├── camera/ detection/ DataClass/ tracking/ embedding/ streaming/
    └── (yolov8n_ncnn_model/ gets baked in at build time - see streaming/export_ncnn.py)
```

Rebuild either service after a code change:
```bash
cd ~/npm/guardsense-relay && docker compose up -d --build
cd ~/npm/guardsense-capture && docker compose up -d --build
```

`guardsense-capture`'s Dockerfile installs CPU-only PyTorch explicitly
(`--index-url https://download.pytorch.org/whl/cpu`) *before* the rest of
`requirements.txt` - otherwise pip's default wheel pulls in CUDA/cuDNN
(a GB+ of unused downloads) even on this ARM board with no NVIDIA GPU,
and pin `torch`+`torchvision` together or you'll hit
`RuntimeError: operator torchvision::nms does not exist` from a version
mismatch.

## 6. Timezone, retention and disk space

- **Timezone:** containers run in UTC by default, so alert/log timestamps
  came out hours off. Set `TZ=<your zone>` under `environment:` in each
  compose file **and** install `tzdata` in the Dockerfile (Debian slim
  doesn't ship it, and `TZ` does nothing without it).
- **Retention** (relay `environment:` / `.env`): `UNASSIGNED_RETENTION_HOURS`
  (default 48) purges never-named crops; `PERSON_MAX_CROPS` (default 300)
  trims each named person to their best crops; `RETENTION_INTERVAL_SECONDS`
  (default 3600) is how often it runs.
- **Capture-side crop limits** (capture `.env`): `CROP_MIN_INTERVAL`,
  `CROPS_PER_TRACK`, `CROPS_PER_TRACK_MATCHED`, `CROP_MIN_HEIGHT/WIDTH`,
  and `SAVE_LOCAL_CROPS` (keep off - it duplicates every crop onto the SD
  card).
- **Disk:** most of the SD card ends up as Docker build cache from repeated
  rebuilds, not GuardSense's data. Check with `docker system df` and
  `du -xh --max-depth=1 ~ | sort -rh | head`; reclaim with
  `docker builder prune -f` (unused cache only - running containers, images
  and data are untouched, the next rebuild is just slower).
- **Local-only access:** to take the public hostname offline, disable (or
  delete) that proxy host in the Nginx Proxy Manager UI rather than editing
  nginx's generated config - NPM regenerates it. The relay still listens on
  `<pi-lan-ip>:8080` on the LAN.

## 7. CI/CD (push to `main` deploys to the Pi)

A **self-hosted GitHub Actions runner** on the Pi runs `deploy/deploy.sh`
after each push to `main`. It makes only *outbound* connections to GitHub, so
no port-forward is needed.

**One-time setup** (on the Pi):
1. In the GitHub repo: Settings -> Actions -> Runners -> **New self-hosted
   runner**, and copy the registration token (valid ~1 hour).
2. On the Pi, from a checkout of the repo (or just the script):
   ```bash
   bash deploy/setup-runner.sh <owner>/<repo> <registration-token>
   ```
   It downloads the arm64 runner, registers it with the label `pi5`, and
   installs a systemd service (`sudo` needed) so it survives reboots.
3. The runner user must be in the `docker` group (`id` should list it).
4. The runner should show as **Idle** in that Runners page.

**What a deploy does:** the `check` job compiles every module on a
GitHub-hosted runner; then the `deploy` job (on the Pi) copies changed files
into `~/npm/guardsense-relay` / `~/npm/guardsense-capture`, rebuilds only the
service(s) that changed, and health-checks them. It never touches `.env`,
`data/` or `yolov8n.pt` (the weights are gitignored, so they must already be
in `~/npm/guardsense-capture/`).

**Run it by hand / preview:**
```bash
bash deploy/deploy.sh              # deploy what changed
DRY_RUN=1 bash deploy/deploy.sh    # show what would change, modify nothing
FORCE=1   bash deploy/deploy.sh    # rebuild both regardless
```
You can also trigger it from the repo's Actions tab ("Run workflow", with a
*force* option).

**Security:** the workflow deliberately has no `pull_request` trigger - a
self-hosted runner runs code on your Pi, so it must never run a fork's PR. If
the repo is public, also set Settings -> Actions -> General -> *Require
approval for outside collaborators*.

**Rolling back:** `git revert <bad-commit>` and push - that redeploys the old
code. There's no automatic rollback.

## 8. Quick commands

```bash
docker ps                                    # all running containers
docker logs -f guardsense-capture            # capture node logs (or use the web UI's /logs page)
docker logs -f guardsense-relay
docker inspect <container> | grep -A5 Networks   # check which network a container is on
curl -k --resolve <domain>:443:<pi-lan-ip> https://<domain>/<path>   # test a public domain from the same LAN (hairpin workaround)
```
