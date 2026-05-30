from flask import Flask, jsonify
import os
import re
import socket
import time
import shutil

app = Flask(__name__)

DISK_PATH = os.environ.get("HPB_MONITOR_DISK_PATH", "/")

# Module-level network-rate baseline: { iface: (ts, rx, tx) }.
# Valid only because gunicorn is pinned to --workers 1.
_NET_BASELINE: dict = {}


def _read_meminfo() -> dict | None:
    """Parse /proc/meminfo into a dict of label -> int (kB). Returns None on failure."""
    try:
        with open("/proc/meminfo", "r") as f:
            raw = f.read()
    except OSError:
        return None
    out = {}
    for line in raw.splitlines():
        m = re.match(r"^([A-Za-z()_]+):\s+(\d+)\s*kB", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out or None


def gather_memory() -> dict | None:
    info = _read_meminfo()
    if info is None:
        return None
    total_kb = info.get("MemTotal")
    avail_kb = info.get("MemAvailable")
    if total_kb is None or avail_kb is None or total_kb <= 0:
        return None
    total_bytes = total_kb * 1024
    used_bytes = max(0, (total_kb - avail_kb) * 1024)
    return {
        "totalBytes": total_bytes,
        "usedBytes": used_bytes,
        "percent": round(used_bytes / total_bytes * 100),
    }


def gather_disk() -> dict | None:
    try:
        usage = shutil.disk_usage(DISK_PATH)
    except OSError:
        return None
    total = usage.total
    used = usage.used
    if total <= 0:
        return None
    return {
        "totalBytes": total,
        "usedBytes": used,
        "percent": round(used / total * 100),
        "path": DISK_PATH,
    }


def _primary_interface() -> str | None:
    """Pick the default-route interface from /proc/net/route (lowest metric,
    ignoring on-link routes). Fallback: first physical-looking iface in
    /proc/net/dev."""
    try:
        with open("/proc/net/route", "r") as f:
            route = f.read()
    except OSError:
        route = ""

    best = None
    best_metric = float("inf")
    for i, line in enumerate(route.splitlines()):
        if i == 0 or not line:
            continue  # header / blank
        cols = line.split()
        if len(cols) < 7:
            continue
        iface, dest, gateway = cols[0], cols[1], cols[2]
        try:
            metric = int(cols[6])
        except ValueError:
            continue
        if dest != "00000000" or gateway == "00000000":
            continue  # not a real default route
        if metric < best_metric:
            best_metric = metric
            best = iface
    if best is not None:
        return best

    # Fallback: first physical iface in /proc/net/dev.
    try:
        with open("/proc/net/dev", "r") as f:
            dev = f.read()
    except OSError:
        return None
    for line in dev.splitlines():
        if ":" not in line:
            continue
        name = line.split(":", 1)[0].strip()
        if name in ("", "lo") or name.startswith(("docker", "veth", "br-")):
            continue
        return name
    return None


def _read_interface_counters(iface: str) -> tuple[int, int] | None:
    """Return (rx_bytes, tx_bytes) for `iface` from /proc/net/dev, or None."""
    try:
        with open("/proc/net/dev", "r") as f:
            dev = f.read()
    except OSError:
        return None
    for line in dev.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        if name.strip() != iface:
            continue
        fields = rest.split()
        if len(fields) < 16:
            return None
        # After the iface name: 0 = rx_bytes, 8 = tx_bytes.
        try:
            return int(fields[0]), int(fields[8])
        except ValueError:
            return None
    return None


def gather_network() -> dict | None:
    iface = _primary_interface()
    if iface is None:
        return None
    counters = _read_interface_counters(iface)
    if counters is None:
        return None
    rx_bytes, tx_bytes = counters

    now = time.time()
    rx_rate: float | None = None
    tx_rate: float | None = None
    prev = _NET_BASELINE.get(iface)
    if prev is not None:
        prev_ts, prev_rx, prev_tx = prev
        dt = now - prev_ts
        # Race guard: avoid divide-by-near-zero spikes from two rapid polls.
        if dt >= 1:
            rx_rate = max(0.0, (rx_bytes - prev_rx) / dt)
            tx_rate = max(0.0, (tx_bytes - prev_tx) / dt)
    _NET_BASELINE[iface] = (now, rx_bytes, tx_bytes)

    return {
        "hostname": socket.gethostname(),
        "interface": iface,
        "rxBytesPerSec": rx_rate,
        "txBytesPerSec": tx_rate,
        "rxBytesTotal": rx_bytes,
        "txBytesTotal": tx_bytes,
    }


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "hpb_monitor"}), 200


@app.route("/stats", methods=["GET"])
def stats():
    return jsonify({
        "memory": gather_memory(),
        "disk": gather_disk(),
        "network": gather_network(),
        "snapshotAt": int(time.time()),
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
