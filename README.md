# hpb_monitor

A tiny Python/Flask service that exposes the memory, disk, and network
metrics of its host as JSON. Designed to be polled by the superadminpage
dashboard so the HPB / signaling host's resource state is visible
alongside the Nextcloud host.

## API

| Route     | Response                                                                                              |
|-----------|-------------------------------------------------------------------------------------------------------|
| `GET /health` | `200 {"status":"ok","service":"hpb_monitor"}`                                                       |
| `GET /stats`  | `200` JSON with `memory`, `disk`, `network`, `snapshotAt`. Any block that can't be read is `null`. |

Example `/stats`:

```json
{
  "memory":  {"totalBytes": 16823357440, "usedBytes": 3214568064, "percent": 19},
  "disk":    {"totalBytes": 107374182400, "usedBytes": 42949672960, "percent": 40, "path": "/"},
  "network": {
    "hostname": "hpb-prod-01", "interface": "eth0",
    "rxBytesPerSec": 12345.0, "txBytesPerSec": 8901.0,
    "rxBytesTotal":  1234567890, "txBytesTotal": 234567890
  },
  "snapshotAt": 1779080000
}
```

`network.rxBytesPerSec` / `txBytesPerSec` are computed by diffing against
an in-process baseline. The first call after start returns `null`; the
second and later calls return real numbers. Gunicorn is pinned to
`--workers 1` so the baseline (a Python dict) is consistent across requests.

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `HPB_MONITOR_DISK_PATH` | `/` | Filesystem path to report under `/stats.disk`. Point at a data volume mount if you want to monitor it instead of root. |

## Security model

No app-level auth. Bring up the included `npm` (nginx-proxy-manager) and
restrict the proxy host to the Nextcloud host's public IP via an Access
List. The monitor publishes no ports to the host, so the only way in is
through npm.

## Deployment

### 1. Pre-flight on the HPB host

Confirm nothing else is already on 80/443:

```bash
ss -ltnp 'sport = :80 or sport = :443'
```

If something else owns those ports, free them before continuing — npm
will fail to start otherwise. The Talk HPB signaling server usually
listens on a non-standard port.

### 2. Bring the stack up

```bash
cd /opt/hpb_monitor    # or wherever you cloned the repo
docker compose up -d --build
docker compose ps
docker compose exec hpb-monitor curl -s localhost:5000/health
docker compose exec hpb-monitor curl -s localhost:5000/stats | python3 -m json.tool
```

Both containers `Up`; `hpb-monitor` should reach `Up (healthy)` within ~40 s.

### 3. Open the npm admin UI via SSH tunnel

```bash
ssh -L 8181:localhost:81 root@46.224.7.132
```

Then browse to `http://localhost:8181`. Default credentials
`admin@example.com` / `changeme`. **Change the password immediately.**

### 4. Create an Access List in npm

Name it `nextcloud-admin-only`. *Access* tab: add an Allow rule with the
Nextcloud host's public IP. *Satisfy* = *All*. Save.

### 5. Create a Proxy Host

Pick a hostname (e.g. `hpb-monitor.loket.site`) and point its DNS A
record at this server. In npm:

- Scheme `http`, Forward Hostname `hpb-monitor`, Forward Port `5000`.
- *SSL* tab → request a Let's Encrypt cert. Tick "Force SSL", "HTTP/2",
  agree to TOS.
- *Access List* tab → select `nextcloud-admin-only`. Save.

### 6. Smoke test from the Nextcloud host

```bash
curl -s https://hpb-monitor.loket.site/health
curl -s https://hpb-monitor.loket.site/stats | jq '{m:.memory.percent, d:.disk.percent, i:.network.interface}'
```

Expected: 200 JSON for both. From any other host the same `curl` should
return `403` (npm Access List rejecting).

### 7. Optional: give the container a readable hostname

By default `network.hostname` in `/stats` is the container ID hash. To
get something like `hpb-prod-01` instead, add to the `hpb-monitor`
service in `docker-compose.yml`:

```yaml
    hostname: hpb-prod-01
```

Then `docker compose up -d` to recreate. No app change needed.
