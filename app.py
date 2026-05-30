from flask import Flask, jsonify
import os
import socket
import time
import shutil

app = Flask(__name__)

DISK_PATH = os.environ.get("HPB_MONITOR_DISK_PATH", "/")

# Module-level network-rate baseline: { iface: (ts, rx, tx) }.
# Valid only because gunicorn is pinned to --workers 1.
_NET_BASELINE: dict = {}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "hpb_monitor"}), 200


@app.route("/stats", methods=["GET"])
def stats():
    # Filled in by Task 3.
    return jsonify({
        "memory": None,
        "disk": None,
        "network": None,
        "snapshotAt": int(time.time()),
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
