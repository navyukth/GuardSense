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
Raspberry Pi 5 - LAN IP e.g. 192.168.0.170 (DHCP reservation recommended)
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

## 6. Quick commands

```bash
docker ps                                    # all running containers
docker logs -f guardsense-capture            # capture node logs (or use the web UI's /logs page)
docker logs -f guardsense-relay
docker inspect <container> | grep -A5 Networks   # check which network a container is on
curl -k --resolve <domain>:443:<pi-lan-ip> https://<domain>/<path>   # test a public domain from the same LAN (hairpin workaround)
```
