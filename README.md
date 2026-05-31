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

No app-level auth. The container publishes only to `127.0.0.1:5050`, so it
is **not** reachable from the internet directly. Access goes through the
host's existing nginx (installed by the sunweaver HPB setup) on
`signaling.loket.site`, where a new `location /monitor/` block restricts
the upstream callers to a single IP — the Nextcloud host that runs
superadminpage.

## Deployment

### 1. Bring the container up

```bash
cd /opt/hpb_monitor    # or wherever you cloned the repo
docker compose up -d --build
docker compose ps      # hpb-monitor reaches "Up (healthy)" within ~40 s

# From the HPB host shell only — confirm the container responds:
curl -s localhost:5050/health
curl -s localhost:5050/stats | python3 -m json.tool
```

The port `5050` is bound to `127.0.0.1` only, so the public IP
`46.224.7.132:5050` is **not** reachable — that's intentional.

### 2. Add the nginx location block

Find the existing `signaling.loket.site` server block (usually
`/etc/nginx/sites-available/signaling.loket.site`, symlinked from
`sites-enabled/`). **Inside** the existing `server { listen 443 ssl; ... }`
block, add:

```nginx
# Path-routed metrics endpoint for hpb_monitor.
# Only the Nextcloud host (superadminpage) is allowed to poll it.
location /monitor/ {
    allow 185.169.252.206;     # Nextcloud host public IP
    deny all;

    proxy_pass http://127.0.0.1:5050/;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout    5s;
    proxy_connect_timeout 3s;
}
```

The trailing `/` on `proxy_pass http://127.0.0.1:5050/;` is required — it
strips the `/monitor/` prefix so a request to
`https://signaling.loket.site/monitor/health` reaches the container as
`/health`. Do **not** drop the trailing slash, and do not remove any
existing `location` directives in the server block.

### 3. Reload nginx safely

```bash
nginx -t                       # config syntax check
systemctl reload nginx
```

If `nginx -t` reports an error, fix and re-run — do **not** reload until
it passes, or signaling will go down for active Talk calls.

### 4. Smoke test from the Nextcloud host (185.169.252.206)

```bash
curl -s https://signaling.loket.site/monitor/health
curl -s https://signaling.loket.site/monitor/stats | jq '{m:.memory.percent, d:.disk.percent, i:.network.interface}'
```

Expected: 200 JSON for both. The network rates are `null` on the first
request and real numbers on subsequent requests.

### 5. Deny test from anywhere else

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://signaling.loket.site/monitor/health
```

Expected: `403` (nginx allow/deny rejecting).

### 6. Sanity-check signaling is unchanged

```bash
curl -s https://signaling.loket.site/         # should respond exactly as before
```

The new `/monitor/` location is purely additive; if anything else on
`signaling.loket.site` started misbehaving after the reload, double-check
you didn't paste the snippet inside a wrong server block (e.g. an HTTP→HTTPS
redirect block instead of the `:443 ssl` block).

### 7. Optional: give the container a readable hostname

By default `network.hostname` in `/stats` is the container ID hash. To
get something like `hpb-prod-01` instead, add to the `hpb-monitor`
service in `docker-compose.yml`:

```yaml
    hostname: hpb-prod-01
```

Then `docker compose up -d` to recreate. No app change needed.
