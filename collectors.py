"""
collectors.py - all data gathering lives here.
These functions return plain dicts/lists; no web framework logic.

Used by: app.py
"""

import json
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path


def _run(cmd, timeout=8):
    """Run a command and return stdout; raise RuntimeError on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"'{' '.join(cmd)}' failed: {proc.stderr.strip()}")
    return proc.stdout


# ---------------------------------------------------------------- overview

def _cpu_temp():
    try:
        raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        return round(int(raw) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def _cpu_pct(sample_s=0.2):
    """CPU busy percentage, computed from two samples of /proc/stat."""

    def read_idle_total():
        with open("/proc/stat") as f:
            vals = [int(v) for v in f.readline().split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
        return idle, sum(vals)

    idle0, total0 = read_idle_total()
    time.sleep(sample_s)
    idle1, total1 = read_idle_total()

    d_total, d_idle = total1 - total0, idle1 - idle0
    if d_total <= 0:
        return None
    return round(100.0 * (d_total - d_idle) / d_total, 1)


def _meminfo():
    """Parse /proc/meminfo into {'key': bytes}."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if parts and parts[0].isdigit():
                info[key] = int(parts[0]) * 1024  # kB -> bytes
    return info


def _model():
    try:
        return Path("/proc/device-tree/model").read_text().strip("\x00\n")
    except OSError:
        return "Unknown device"


def _os_pretty():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "Unknown OS"


def overview():
    mem = _meminfo()
    ram_total = mem.get("MemTotal", 0)
    ram_available = mem.get("MemAvailable", ram_total)
    with open("/proc/uptime") as f:
        uptime_seconds = int(float(f.read().split()[0]))

    return {
        "hostname": socket.gethostname(),
        "model": _model(),
        "os": _os_pretty(),
        "kernel": platform.release(),
        "cpu_pct": _cpu_pct(),
        "cpu_temp_c": _cpu_temp(),
        "ram_used_bytes": ram_total - ram_available,
        "ram_total_bytes": ram_total,
        "uptime_seconds": uptime_seconds,
        "collected_at": int(time.time()),
    }


# ------------------------------------------------------------- systemd services

_PROP_LIST = [
    "Id", "LoadState", "ActiveState", "SubState", "MainPID",
    "ExecMainStartTimestamp", "MemoryCurrent", "Description",
]


def services(entries):
    """
    entries: list of {"name": str, "display_name": str|None} (systemd units).
    Returns a list with one record per entry, including not-found ones.
    """
    if not entries:
        return []

    names = [e["name"] for e in entries]
    out = _run(
        ["systemctl", "show", *names,
         "--property=" + ",".join(_PROP_LIST), "--no-pager"]
    )

    # Output is blocks of key=value lines, one block per unit.
    units, block = {}, {}
    for line in out.splitlines():
        if not line.strip():
            if block:
                units[block.get("Id", "")] = block
                block = {}
            continue
        key, _, val = line.partition("=")
        block[key] = val
    if block:
        units[block.get("Id", "")] = block

    result = []
    for entry in entries:
        name = entry["name"]
        block = (
            units.get(name)
            or units.get(name + ".service")
            or units.get(name.rstrip(".service"))
            or {}
        )

        load_state = block.get("LoadState", "unknown")
        if load_state in ("not-found", "masked", "error"):
            state = "not-found"
        else:
            state = block.get("ActiveState", "unknown").lower()

        mem_raw = block.get("MemoryCurrent", "")
        memory_bytes = int(mem_raw) if mem_raw.isdigit() else None

        since_ts = None
        ts = block.get("ExecMainStartTimestamp", "")
        if ts:
            for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S %z"):
                try:
                    since_ts = int(datetime.strptime(ts, fmt).timestamp())
                    break
                except ValueError:
                    continue

        pid_raw = block.get("MainPID", "")
        result.append({
            "type": "systemd",
            "name": name,
            "unit": block.get("Id", name),
            "display_name": entry.get("display_name") or name,
            "description": block.get("Description", ""),
            "state": state,
            "sub_state": block.get("SubState", ""),
            "pid": int(pid_raw) if pid_raw.isdigit() else None,
            "since_ts": since_ts,
            "memory_bytes": memory_bytes,
        })

    return result


# ------------------------------------------------------------- docker containers

# docker state -> dashboard state vocabulary
_DOCKER_STATE_MAP = {
    "running":    "active",
    "restarting": "degraded",
    "paused":     "degraded",
    "exited":     "inactive",
    "dead":       "failed",
    "created":    "inactive",
}

_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def _parse_docker_size(text):
    """'53.4MiB' -> bytes (int) or None."""
    if not text:
        return None
    for unit, mult in _SIZE_UNITS.items():
        if text.endswith(unit):
            try:
                return int(float(text[: -len(unit)]) * mult)
            except ValueError:
                return None
    return None


def _parse_cpu_perc(text):
    """'0.12%' -> 0.12 (float) or None."""
    try:
        return round(float(text.rstrip("%")), 2)
    except (AttributeError, ValueError):
        return None


def _parse_mem_usage(text):
    """'53.4MiB / 1.944GiB' -> (used_bytes, limit_bytes)."""
    if not text or "/" not in text:
        return None, None
    used_s, _, limit_s = text.partition("/")
    return _parse_docker_size(used_s.strip()), _parse_docker_size(limit_s.strip())


def docker_containers(entries):
    """
    entries: list of {"name": str (container name), "display_name": str|None}.
    Returns one record per entry, including not-found ones.
    Uses `docker ps -a` for state and `docker stats --no-stream` for CPU/RAM.
    """
    if not entries:
        return []

    docker_bin = shutil.which("docker")

    # Current container states: {name: ps-record}
    ps_records = {}
    if docker_bin:
        try:
            out = _run([docker_bin, "ps", "-a", "--format", "{{json .}}"])
            for line in out.splitlines():
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    ps_records[rec.get("Names", "")] = rec
        except (RuntimeError, ValueError, json.JSONDecodeError):
            pass  # daemon down etc. -> everything becomes unavailable below

    # Live stats only for containers that are currently running
    running_requested = [e["name"] for e in entries if e["name"] in ps_records
                         and ps_records[e["name"]].get("State") == "running"]
    stats = {}
    if docker_bin and running_requested:
        try:
            out = _run(
                [docker_bin, "stats", "--no-stream",
                 "--format", "{{json .}}", *running_requested],
                timeout=15,
            )
            for line in out.splitlines():
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    stats[rec.get("Name", "")] = rec
        except (RuntimeError, ValueError, json.JSONDecodeError):
            pass  # stats are best-effort

    daemon_unreachable = docker_bin is None or not ps_records

    result = []
    for entry in entries:
        name = entry["name"]
        ps = ps_records.get(name)
        st = stats.get(name, {})

        if ps is None:
            state = "unavailable" if daemon_unreachable else "not-found"
            description = ("Docker daemon unreachable" if daemon_unreachable
                           else "Container not found on this host")
            mem_used = mem_limit = None
            cpu_pct = None
            status_text = ""
            image = ""
        else:
            state = _DOCKER_STATE_MAP.get(ps.get("State", ""), "degraded")
            description = ps.get("Status", "")
            mem_used, mem_limit = _parse_mem_usage(st.get("MemUsage", ""))
            cpu_pct = _parse_cpu_perc(st.get("CPUPerc"))
            status_text = ps.get("Status", "")
            image = ps.get("Image", "")

        result.append({
            "type": "docker",
            "name": name,
            "unit": name,
            "display_name": entry.get("display_name") or name,
            "description": description,
            "state": state,
            "sub_state": ps.get("State", "") if ps else "",
            "pid": None,
            "since_ts": None,
            "status_text": status_text,
            "image": image,
            "cpu_pct": cpu_pct,
            "memory_used_bytes": mem_used,
            "memory_limit_bytes": mem_limit,
        })

    return result


# ---------------------------------------------------------------- storage

def _df_map():
    """Parse `df -B1 -P` into {mount_point: {size, used, avail}} in bytes."""
    result = {}
    for line in _run(["df", "-B1", "-P"]).splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 6 and cols[1].isdigit():
            result[cols[5]] = {
                "size": int(cols[1]),
                "used": int(cols[2]),
                "avail": int(cols[3]),
            }
    return result


def storage():
    """
    All mounted partitions (internal + external).
    External = the drive (or any ancestor) reports removable, per lsblk's RM flag.
    """
    ls = json.loads(_run([
        "lsblk", "-b", "-J", "-o",
        "NAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINT,RM,MODEL,SERIAL",
    ]))
    df_map = _df_map()
    drives = []

    def _rm_flag(node):
        try:
            return int(node.get("rm") or 0) == 1
        except (TypeError, ValueError):
            return False

    def walk(node, parent_removable=False):
        removable = _rm_flag(node) or parent_removable
        if node.get("type") == "part" and node.get("mountpoint"):
            mount = node["mountpoint"]
            usage = df_map.get(mount, {})
            size = int(node.get("size") or 0)
            drives.append({
                "device": node.get("path") or "/dev/" + node["name"],
                "model": (node.get("model") or "").strip() or None,
                "size_bytes": size,
                "is_external": removable,
                "fstype": node.get("fstype") or None,
                "mount": mount,
                "used_bytes": usage.get("used", max(size - usage.get("avail", 0), 0)),
                "avail_bytes": usage.get("avail", 0),
            })
        for child in node.get("children") or []:
            walk(child, removable)

    for disk in ls.get("blockdevices", []):
        walk(disk)

    drives.sort(key=lambda d: (d["mount"] != "/", d["mount"]))
    return drives