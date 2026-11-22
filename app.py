"""
app.py - minimal Flask app for the read-only Pi dashboard.
Routes:
  GET /                -> index page
  GET /api/dashboard   -> { overview, services, storage }
  GET /api/overview
  GET /api/services    -> unified list (systemd + docker, per config)
  GET /api/storage

Run:  python3 app.py          (dev)
Prod: gunicorn -w 1 -b 0.0.0.0:5000 app:app
"""

import fnmatch
import logging
from pathlib import Path

import yaml
from flask import Flask, jsonify, render_template

import collectors

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.yaml"

app = Flask(__name__)
log = logging.getLogger("pi-dashboard")


def load_config():
    """Re-read config on every request so edits apply without a restart."""
    cfg = {"services": [], "ignored_patterns": []}
    try:
        with open(CONFIG_FILE) as f:
            raw = yaml.safe_load(f) or {}
            cfg.update(raw)
    except FileNotFoundError:
        log.warning("config.yaml not found, using defaults")
    return cfg


def _match_pattern(name, pattern):
    """glob-style match, case-insensitive; * and ? wildcards."""
    return fnmatch.fnmatch(name.lower(), pattern.lower())


def visible_services(cfg):
    """
    Split the configured service entries by backend type, applying:
      enabled: false   -> keep configured but hide
      ignored_patterns -> filter regardless of enabled flag
    Each entry: {name, display_name, type} with type in {systemd, docker}.
    Unknown types fall back to systemd.
    """
    out = {"systemd": [], "docker": []}
    for entry in cfg.get("services") or []:
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        if not entry.get("enabled", True):
            continue
        if any(_match_pattern(name, p) for p in cfg.get("ignored_patterns") or []):
            continue
        backend = entry.get("type", "systemd")
        if backend not in out:
            backend = "systemd"
        out[backend].append({
            "name": name,
            "display_name": entry.get("display_name") or name,
        })
    return out


@app.route("/")
def index():
    return render_template("index.html")


def _unified_services(cfg):
    """
    Merge systemd services and docker containers into one list.
    Order: as listed in config (systemd first, then docker).
    If a collection raises (e.g. daemon hiccup), degrade that section
    instead of failing the whole endpoint.
    """
    backends = visible_services(cfg)
    result = []
    try:
        result.extend(collectors.services(backends["systemd"]))
    except Exception as exc:
        log.warning("systemd collection failed: %s", exc)
        result.extend(_unavailable(backends["systemd"], "systemd", str(exc)))
    try:
        result.extend(collectors.docker_containers(backends["docker"]))
    except Exception as exc:
        log.warning("docker collection failed: %s", exc)
        result.extend(_unavailable(backends["docker"], "docker", str(exc)))
    return result


def _unavailable(entries, backend, reason):
    """Placeholder records when a collector itself blows up."""
    return [{
        "type": backend,
        "name": e["name"],
        "unit": e["name"],
        "display_name": e.get("display_name") or e["name"],
        "description": f"collection error: {reason}",
        "state": "unavailable",
        "sub_state": "",
        "pid": None,
        "since_ts": None,
        "cpu_pct": None,
        "memory_bytes": None,
    } for e in entries]


@app.route("/api/dashboard")
def api_dashboard():
    cfg = load_config()
    payload = {
        "overview": collectors.overview(),
        "services": _unified_services(cfg),
        "storage": collectors.storage(),
    }
    return jsonify(payload)


@app.route("/api/overview")
def api_overview():
    return jsonify(collectors.overview())


@app.route("/api/services")
def api_services():
    return jsonify(_unified_services(load_config()))


@app.route("/api/storage")
def api_storage():
    return jsonify(collectors.storage())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)