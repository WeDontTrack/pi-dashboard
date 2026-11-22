# pi-dashboard

![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi%20%7C%20Linux-c01c28?logo=raspberry-pi&logoColor=white)
![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-All%20rights%20reserved-lightgrey)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)

A tiny, dependency-light, **read-only web dashboard** for a Raspberry Pi home
server — see at a glance which services are running (both **systemd units**
and **Docker containers**, unified in one view), how loaded the Pi is, and
how full your storage drives are.

No agents, no databases, no cloud, no build toolchain. Eight small files you
can read in one sitting.

<!-- ## Screenshot
![Dashboard screenshot](docs/screenshot.png)
TODO: add screenshot of the Dashboard tab before/after publishing. -->

---

## Why?

Existing home-server dashboards tend to monitor *everything* — dozens of
processes, dense graphs, heavy daemons. I wanted the opposite: a
lightweight page that shows **only the services I actually run**, chosen in
a config file, with live status and barely any load on the Pi.

So this project became:

- **Compute-on-demand** — the backend collects fresh data only when someone
  is actually looking. Close the tab, and the Pi does nothing. No cron, no
  background loops.
- **Config-driven service list** — you declare what matters; everything
  else is ignored. Hide a service without deleting it via an `enabled`
  toggle, or silence whole categories with wildcard patterns.
- **Unified services view** — modern setups mix systemd services and Docker
  containers; this dashboard treats them as one list with type badges.
- **Read-only by design** — nothing on this page can poke your server.

It runs great on a Raspberry Pi 5, and equally on any Linux box with
systemd and/or Docker.

---

## What you get

**Dashboard tab** — CPU load, temperature, memory, uptime as KPI cards;
service cards with green/amber/red status dots (Docker cards show live CPU %
and memory); drive cards with usage bars.

**Services tab** — detailed table: state, uptime, PID, memory, CPU
(containers), per-service description.

**Storage tab** — every mounted partition (internal + external, tagged as
such): device, mount point, filesystem, size, used/free with bars.

Polling is polite: data refreshes every 30 seconds **only while the browser
tab is visible**, and every page load is guaranteed fresh data.

---

## Quick start

Tested on Raspberry Pi OS (Trixie); works on any Linux with Python 3.9+.

```bash
# 1. Clone
git clone https://github.com/wedonttrack/pi-dashboard.git
cd pi-dashboard

# 2. Virtual environment + dependencies
python3 -m venv venv
venv/bin/pip install -r requirements.txt gunicorn

# 3. Point the config at YOUR services
nano config.yaml        # see "Configuration" below and config.example.yaml for example config file

# 4. Run it
venv/bin/python app.py
```

Open `http://<pi-ip>:5000` from any device on your network. Done.

> **Using Docker on the Pi?** The user running the app must be able to talk
> to the Docker daemon: `sudo usermod -aG docker $USER`, then log out and
> back in. (For a more secure alternative, see *Security hardening* below.)

---

## Configuration

Everything you see is controlled by `config.yaml`:

```yaml
services:
  # systemd services
  - name: sshd                      # unit name, .service suffix optional
    display_name: "SSH Server"
    type: systemd                   # default when omitted
    enabled: true                   # false = hidden, but kept in config

  # docker containers
  - name: pihole
    display_name: "Pi-hole"
    type: docker                    # name must match `docker ps` output
    enabled: true

ignored_patterns:                   # wildcard hide-list, applies to any type
  - "*getty*"
  - "user@*.service"
  - "*.mount"
```

| Field | Values | Effect |
|---|---|---|
| `name` | systemd unit or container name | The service/container to show |
| `type` | `systemd` *(default)* or `docker` | Which backend to query |
| `display_name` | any string | Friendly label (defaults to name) |
| `enabled` | `true` / `false` | `false` hides the entry without deleting it — flip it back any time |
| `ignored_patterns` | globs with `*`, `?` | Hides anything matching, regardless of `enabled` |

Config is re-read on **every request**: edit the file, refresh the browser —
no restarts, ever.

A service listed in the config but missing on the system shows up as red
**"not found"** rather than silently vanishing — so you notice when
something dies or a typo sneaks in.

---

## Run at boot (systemd)

```bash
sudo cp pi-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-dashboard
```

Edit the `User=`, `WorkingDirectory=` and `ExecStart=` lines in the unit to
match your username and install path first.

Production serving uses gunicorn (`ExecStart=...gunicorn -w 1 -b
0.0.0.0:5000 app:app`). Gunicorn is the WSGI HTTP server that imports your
Flask app — that's why you'll find no `import gunicorn` in the code.

---

## Security hardening (optional but recommended)

The quick start runs the dashboard as your user — fine for tinkering. For a
long-lived setup, run it as a dedicated **low-privilege service account**
instead. Two important facts shaped this design:

1. Never give the service user sudo rights.
2. Never add the service user to the `docker` group — **that group is
   root-equivalent** (any member can escape to host root by bind-mounting
   `/` into a container).

Instead, the dashboard reaches Docker through a **read-only socket proxy**
that allows only the two API paths it needs, bound to loopback only:

```bash
# 1. Dedicated service user: no sudo, no shell, no docker group
sudo useradd --system --home-dir /opt/pi-dashboard \
  --shell /usr/sbin/nologin dashsvc
sudo passwd -l dashsvc

# 2. Move the app out of your home directory
sudo mkdir -p /opt/pi-dashboard
sudo cp -r ~/pi-dashboard/. /opt/pi-dashboard/
sudo chown -R dashsvc:dashsvc /opt/pi-dashboard
sudo -u dashsvc python3 -m venv /opt/pi-dashboard/venv
sudo -u dashsvc /opt/pi-dashboard/venv/bin/pip install -r requirements.txt gunicorn

# 3. Read-only Docker socket proxy (loopback only)
sudo docker run -d --name socket-proxy --restart unless-stopped \
  -p 127.0.0.1:2375:2375 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -e POST=0 -e CONTAINERS=1 -e PING=1 -e VERSION=1 \
  lscr.io/linuxserver/socket-proxy:latest

# Verify: list works...
DOCKER_HOST=tcp://127.0.0.1:2375 docker ps
# ...but mutations are denied (403):
DOCKER_HOST=tcp://127.0.0.1:2375 docker pull alpine   # must FAIL

# 4. Update pi-dashboard.service:
#    User=dashsvc / Group=dashsvc
#    WorkingDirectory=/opt/pi-dashboard
#    Environment=DOCKER_HOST=tcp://127.0.0.1:2375
sudo systemctl daemon-reload
sudo systemctl restart pi-dashboard
```

What the compromised-account worst case now looks like: an attacker gets a
user that owns one folder, cannot sudo, cannot login, and can *read* Docker
container states — nothing else.

---

## Architecture in one paragraph

A Flask backend (`app.py`) re-reads `config.yaml` per request, then gathers
data through standard Linux interfaces: CPU/RAM/uptime from `/proc`, the SoC
temperature from `/sys`, service state from `systemctl show`, drives from
`lsblk` + `df`, and containers from the Docker CLI via `DOCKER_HOST`. One
combined JSON endpoint (`/api/dashboard`) feeds a dependency-free frontend
(vanilla HTML/CSS/JS, three tabs) that polls every 30 s while visible. If a
collector fails (say, the Docker daemon is down), only its section degrades
to an "unavailable" status — the rest of the dashboard keeps working.

```
Browser ──> gunicorn (:5000) ──> Flask app ──> collectors
                                    │             ├─ /proc, /sys, lsblk, df
                                    │             ├─ systemctl show …
                                    └─ config.yaml└─ docker CLI ──> socket proxy ──> docker.sock
```

For more information see [Design.md](./Desgin.md)

---

## API

| Endpoint | Returns |
|---|---|
| `GET /api/dashboard` | `{overview, services[], storage[]}` — everything in one call |
| `GET /api/overview` | Host vitals (hostname, model, OS, CPU %, temp, RAM, uptime) |
| `GET /api/services` | Unified systemd + Docker service list |
| `GET /api/storage` | All mounted drives with usage |

Handy for building your own frontend or hooking into home automation.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "offline" badge in the header | App not running or wrong port — check `systemctl status pi-dashboard` and `journalctl -u pi-dashboard -n 20` |
| All containers "unavailable" | Docker daemon down, missing CLI, or (hardened setup) socket-proxy not running / `DOCKER_HOST` not set in the unit |
| Service "not found" though it runs | Name mismatch — copy the exact name from `systemctl list-units --type=service --state=running` or `docker ps` (case matters) |
| Config edit not appearing | It applies on next page load; a hard refresh (Ctrl+Shift+R) also busts any cached assets |
| Page loads unstyled | `static/style.css` missing or misnamed — `curl -I http://localhost:5000/static/style.css` should say 200 |

---

## Contributing

Issues and pull requests are welcome — this is intentionally small, so the
bar is "does it stay small and dependency-light?" Good first contributions:

- Additional collectors (per-service CPU for systemd, SMART health for drives)
- Light/dark theme toggle
- Compose-stack grouping via container labels

## Credits

Designed and built as a pair-programming project with an AI assistant
([Lumo](https://lumo.proton.me)) — architecture decisions, security review
and implementation were human-approved at every step. Session habits worth
borrowing: read-only first, config over code, and threat-model *before*
granting any group membership.

---

## License

All rights reserved. You are welcome to fork and self-host for personal
use; contact the author for redistribution rights.
