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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "hpb_monitor"}), 200


@app.route("/stats", methods=["GET"])
def stats():
    return jsonify({
        "memory": gather_memory(),
        "disk": gather_disk(),
        "network": None,
        "snapshotAt": int(time.time()),
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
