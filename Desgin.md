# Solution Document — Raspberry Pi Home Server Monitoring Dashboard

| | |
|---|---|
| **Document title** | Raspberry Pi Home Server Monitoring Dashboard — Solution Design & Operations |
| **Version** | 1.0 |
| **Date** | 21 November 2026 |
| **Status** | Released (v1 frozen; roadmap items pending) |
| **Classification** | Private — household infrastructure |
| **Audience** | Owner/operator, co-administrators, future rebuild reference, portfolio |

---

## Table of Contents

1. Executive Summary
2. Background & Problem Statement
3. Objectives, Scope & Non-Goals
4. Requirements
5. Stakeholders & Usage Context
6. Solution Overview
7. Technology Evaluation & Decisions
8. Detailed Design
9. Security Design
10. Deployment Procedure
11. Testing & Verification
12. Operations Manual
13. Known Issues & Lessons Learned
14. Future Roadmap
15. Appendices (File Inventory, API Schema, Config Reference, Glossary)

---

## 1. Executive Summary

This project delivers a custom, read-only web dashboard for a Raspberry Pi 5
home server. The dashboard presents, in a single browser page: system vitals
(CPU load, temperature, memory, uptime), the status of selected *services* —
both systemd units and Docker containers, unified in one view — and the
state of all storage drives (internal and externally attached).

The solution was deliberately designed as a **lightweight, self-owned
alternative to off-the-shelf monitoring suites**: the final target is a
dashboard restricted to *their chosen* services, with a configurable
hide/enable model, minimal resource usage on the Pi, and no cloud
dependencies. It is implemented as a Flask (Python) application served by
gunicorn under systemd, with a dependency-free static frontend and a
JSON API. All data is collected on-demand at request time from standard
Linux interfaces (`systemctl`, `lsblk`, `df`, `/proc`, `/sys`) and the
Docker CLI — no agents, no daemons, no databases.

**Security is a first-class concern rather than an afterthought**: the dashboard
runs under a dedicated, unprivileged service account (`dashsvc`) with no
sudo rights, no login shell, and no direct Docker access; Docker data is
reached through a loopback-bound, read-only socket proxy that whitelists
only the two API paths the dashboard needs.

Version 1 is scoped to LAN-only access with read-only semantics. Remote
access for approximately 20–30 household members abroad is designed
(wg-easy on the Pi, chosen after evaluating Tailscale, Headscale, and
Cloudflare Tunnel) and scheduled as the next release phase.

---

## 2. Background & Problem Statement

### 2.1 Environment

The owner operates a Raspberry Pi 5 as a multi-purpose home server. The
device hosts a growing number of workloads, the majority running as Docker
containers, with the remainder as native systemd services. Storage comprises
the primary SD/boot medium plus externally attached drives.

> **Placeholder to complete on rebuild:** exact OS release
> (`cat /etc/os-release`), RAM configuration, hostname, and static/DHCP IP
> allocation method were not recorded in the project conversation.

### 2.2 Problem

Before this project there was no single, quick way to answer, at a glance:

- Are my services running right now?
- How loaded is the Pi (CPU, memory, thermals)?
- How full are my drives, and which are external?

Existing options were unsatisfying for specific reasons: full monitoring
platforms (Netdata, Cockpit, Glances) are heavyweight, noisy, oriented
toward *everything* rather than *chosen* services, and some are
resource-hungry on ARM; ad-hoc shell checking requires SSH into the box for
every question. The target was to build in-house tool that is lightweight and does not depend on any external bloat services.

### 2.3 Constraints

| Constraint | Impact on design |
|---|---|
| Read-only dashboard (no control actions) in v1 | Simplifies security posture; no write endpoints |
| Must remain light on Pi resources | No persistent collection loops, no databases; compute-on-request |
| Services list must be config-driven, with hide-without-delete toggling | `config.yaml` with `enabled` flags and `ignored_patterns` |
| Multiple services run as Docker containers, not just systemd units | Unified service view spanning both backends |
| Server must ultimately be shared with 20–30 specific people worldwide, but stay private | Remote-access layer designed but deferred; excludes public port-forwarding of the dashboard |
| Target prefers incremental delivery ("basic setup first, make it work, add features later") | Phased roadmap; v1 explicitly frozen |

---

## 3. Objectives, Scope & Non-Goals

### 3.1 Objectives (v1)

1. Single-page dashboard, three tabs: Dashboard (vitals + service cards +
   drive cards), Services (detail table), Storage (cards + mount table).
2. Data freshness: computed live on page load; browser polls every 30 s
   only while the tab is visible; always-fresh on hard refresh.
3. Service visibility governed entirely by `config.yaml`.
4. Support both systemd units and Docker containers in one unified list.
5. Read-only: no start/stop/restart controls anywhere.
6. Survive reboots unattended (systemd unit).
7. Run under a dedicated low-privilege service account.

### 3.2 Out of scope (explicit non-goals for v1)

- Any control actions (start/stop/restart) — read-only by design.
- Authentication on the dashboard (deferred to the remote-access phase,
  which supplies its own access gate).
- Time-series history/graphs (no persistence layer exists).
- Alerting/notifications.
- IPv6/exotic edge cases of drive enumeration (e.g. snap loop mounts are
  excluded by construction).

---

## 4. Requirements

### 4.1 Functional requirements

| ID | Requirement | Status in v1 |
|---|---|---|
| FR-1 | Display host identity: hostname, Pi model, OS, kernel | ✅ |
| FR-2 | Display CPU utilisation (%), sampled from `/proc/stat` deltas | ✅ |
| FR-3 | Display SoC temperature | ✅ |
| FR-4 | Display memory used/total | ✅ |
| FR-5 | Display uptime | ✅ |
| FR-6 | Show each *configured* service: state, uptime, memory; Docker containers additionally CPU % and memory used/limit | ✅ |
| FR-7 | Services configured but absent from the system must be visibly marked (`not-found`), never silently dropped | ✅ |
| FR-8 | Docker daemon unreachable must be distinguishable from container-absent (`unavailable` vs `not-found`) | ✅ |
| FR-9 | Config entries with `enabled: false` are hidden but preserved; `ignored_patterns` hide by wildcard regardless of flag | ✅ |
| FR-10 | List all mounted partitions with device, mount, filesystem, size, used, free, internal/external classification | ✅ |
| FR-11 | Config edits take effect on next page load without service restart | ✅ |
| FR-12 | Polling pauses when the browser tab is hidden | ✅ |

### 4.2 Non-functional requirements

| ID | Requirement | Implementation |
|---|---|---|
| NFR-1 | Minimal Pi resource footprint | One gunicorn worker; compute-on-request; no cron/background collection |
| NFR-2 | No external/cloud dependencies | All collection local; frontend has zero build/npm toolchain |
| NFR-3 | Least privilege | `dashsvc` system user + Docker socket proxy (§9) |
| NFR-4 | Resilience of collection | A failing backend (e.g. Docker hiccup) degrades its own section only; API never fails wholesale |
| NFR-5 | Simplicity/auditability | 8 small files; no frameworks beyond Flask/PyYAML/gunicorn |
| NFR-6 | Automatic recovery | systemd `Restart=on-failure` |

---

## 5. Stakeholders & Usage Context

| Stakeholder | Role | Access |
|---|---|---|
| Owner | Sole administrator; SSH-only administration | Full, via SSH and dashboard |
| Household members (planned, ~20–30) | View-only consumers of the dashboard | Future: via wg-easy tunnel only |
| Co-administrator | Documented secondary operator | SSH + this document |

Usage pattern: owners/co-admins consult the dashboard on LAN from any
browser; in future, invited members view it remotely. Nobody is expected to
hold SSH credentials, hence the read-only guarantee.

---

## 6. Solution Overview

### 6.1 Architecture

```
Browser (any device on LAN)
    │  http://<pi-ip>:5000        poll every 30s while tab visible
    ▼
gunicorn (1 worker, port 5000)  ── runs as: dashsvc (no sudo, no shell)
    │
    ├── Flask app (app.py)
    │     ├── load config.yaml (per request; edits apply without restart)
    │     ├── GET /            → templates/index.html
    │     ├── GET /api/dashboard → {overview, services, storage}
    │     └── GET /api/{overview,services,storage}
    │
    └── collectors.py
          ├── overview():  /proc/stat, /proc/meminfo, /proc/uptime,
          │                /sys/class/thermal/thermal_zone0,
          │                /proc/device-tree/model, /etc/os-release
          ├── services():  systemctl show <units> --properties=…
          ├── docker_containers(): docker ps -a --format '{{json .}}'
          │                          docker stats --no-stream (running only)
          │                └─ via DOCKER_HOST=tcp://127.0.0.1:2375
          │                   (socket proxy: GET-only whitelist)
          └── storage():   lsblk -b -J + df -B1 -P (merged)
```

### 6.2 Request/data flow (v1 semantics)

There is **no scheduled collection anywhere in the system**. Every API call
computes fresh data (≈100 ms for systemd/storage; docker stats adds ~1–2 s).
The browser's 30-second interval poll stops entirely when the tab is hidden
(`visibilitychange`), so an abandoned tab costs nothing. This satisfies the
owner's freshness requirement ("always fresh on hard refresh") more cheaply
than any scheduled-poll design.

### 6.3 Component inventory

| Component | Technology | Responsibility |
|---|---|---|
| `app.py` | Flask | Routing, config loading, backend unification, degradation handling |
| `collectors.py` | Python stdlib | All data acquisition from Linux + Docker |
| `config.yaml` | YAML | Service allowlist, type discrimination, toggles, ignore patterns |
| `templates/index.html` | Static HTML | Single page, three tabs |
| `static/style.css` | Static CSS | Dark-theme responsive styling |
| `static/app.js` | Vanilla JS | Fetch, render, poll scheduling, connection indicator |
| `pi-dashboard.service` | systemd unit | Boot persistence + hardening sandbox |
| `requirements.txt` | pip | Flask, PyYAML (+ gunicorn installed alongside) |
| `socket-proxy` container | linuxserver/socket-proxy | Read-only Docker API gateway (loopback only) |

---

## 7. Technology Evaluation & Decisions

### 7.1 Application stack

Options evaluated:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Shell scripts + cron → static JSON | Zero deps, trivially auditable | Painful JSON from bash; every new data source is scripting pain | Rejected |
| **Python Flask + static frontend** | Pre-installed toolchain on Pi OS; clean parsing of `/proc`, `lsblk`, systemctl; trivial growth path | ~Small venv footprint | **Chosen** |
| Node.js + Express | Good realtime story | Heavier on Pi; same child-process work as Python with more overhead | Rejected |
| Off-the-shelf (Glances/Netdata/Cockpit) | 10-minute setup | Monitors *everything* not *chosen services*; defeats purpose; Netdata heavy on ARM | Rejected |

FastAPI/async was considered and passed over: the dashboard is read-only
with short synchronous computations — async would add complexity with no
benefit. Flask's synchronous model fits.

**Gunicorn** (explicit design note, raised by the owner): Flask's built-in
server is a development tool. Gunicorn is the WSGI HTTP server that listens
on port 5000 and *imports* the app (`app:app`), which is why no Python code
imports gunicorn. For strict necessity a single-user read-only dashboard
could survive on Flask's dev server, but gunicorn was retained: it is
already wired into the service unit, provides clean worker handling, and
costs nothing to operate.

### 7.2 Refresh strategy

Considered: server-side scheduled collection (cron/daemon) vs
compute-per-request. The owner's requirement — daily/half-daily staleness
tolerable, but fresh on demand — is best met by compute-per-request plus a
visibility-aware browser poll: always-fresh, zero idle cost, no cron.

### 7.3 Docker integration model

Owner decisions (recorded from design session):

- **Scope:** config-listed containers only, same governance model as
  systemd services (type discriminator + `enabled` + `ignored_patterns`).
- **Detail:** status **plus live CPU/memory** via `docker stats --no-stream`.
- **Presentation:** one unified Services view with type badges (not separate
  sections); flat container list (no compose-project grouping in v1).

Collection method: Docker CLI with JSON formatting (`--format '{{json .}}'`),
invoked with `DOCKER_HOST` pointing at the socket proxy. Alternatives
(Docker SDK for Python, mounting the socket directly) were rejected in
favour of the proxy gateway for least-privilege reasons (§9).

### 7.4 Remote access (designed, not yet implemented)

Requirement: ~20–30 named users abroad, server stays private, owner accepts
"IT department" duties. Options analysed:

| Option | Assessment |
|---|---|
| Tailscale (SaaS) | Ideal UX but free plan caps at ~3 users — disqualifying |
| Port-forwarding the dashboard directly | Rejected outright: public exposure of the app |
| **wg-easy on the Pi** | Unlimited peers; exposed UDP port is cryptographically silent to non-peers; per-peer QR configs; revocation in one click. **Chosen.** Requires: one UDP port-forward (51820), DDNS (e.g. DuckDNS) |
| Headscale (self-hosted Tailscale control) | Strong option but wants an always-on third host (Oracle free tier / small VPS); chicken-and-egg risk if hosted on the Pi itself; reserved for future multi-device needs |
| Cloudflare Tunnel + Access | Viable alternative (free tier ≈50 users, browser-only clients) but adds a third-party transit party and domain dependency |

Decision: **wg-easy** for the next phase. This decision does not alter the
dashboard application; it is purely an access layer.

---

## 8. Detailed Design

### 8.1 Data collection map

| Data | Source | Notes |
|---|---|---|
| CPU % | Two samples of `/proc/stat` (idle+iowait delta), 0.2 s apart | Per-request sampling; no history |
| Temperature | `/sys/class/thermal/thermal_zone0/temp` (milli-°C) | Frontend colours ≥50 °C warm, ≥80 °C hot |
| Memory | `/proc/meminfo`: `MemTotal` − `MemAvailable` | |
| Uptime | `/proc/uptime` | |
| Model / OS | `/proc/device-tree/model`, `/etc/os-release` | |
| Service state | `systemctl show <unit> --property=Id,LoadState,ActiveState,SubState,MainPID,ExecMainStartTimestamp,MemoryCurrent,Description` | Batched single invocation for all units; LoadState `not-found/masked/error` → `state:"not-found"` |
| Container state | `docker ps -a --format '{{json .}}'` | One call for all containers |
| Container CPU/RAM | `docker stats --no-stream --format '{{json .}}'` | Only for currently-running containers; parses human units (`MiB`, `%`) |
| Drives | `lsblk -b -J -o NAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINT,RM,MODEL,SERIAL` merged with `df -B1 -P` | Only `type=part` with a `mountpoint`; `RM` flag inherited from parent disk ⇒ `is_external`; sorted root-first |

### 8.2 State vocabulary (single vocabulary across backends)

| Backend state | Dashboard state | UI rendering |
|---|---|---|
| systemd `active` / docker `running` | `active` | Green dot, "running" |
| `activating`, `reloading` / docker `restarting`, `paused` | `degraded` | Amber, "starting"/"degraded" |
| systemd `failed` / docker `dead` | `failed` | Red, "failed" |
| `inactive` / docker `exited`, `created` | `inactive` | Grey, "stopped" |
| unit absent / masked | `not-found` | Red, "not found" |
| collector failure (e.g. daemon down) | `unavailable` | Red, "unavailable" |

### 8.3 Configuration model

```yaml
services:
  - name: sshd                      # systemd unit (suffix optional)
    display_name: "SSH Server"
    type: systemd                   # default when omitted
    enabled: true                   # false = hide, keep config
  - name: pihole
    display_name: "Pi-hole"
    type: docker                    # name must match `docker ps`
    enabled: true

ignored_patterns:                   # glob, case-insensitive, any type
  - "*getty*"
  - "user@*.service"
  - "*.mount"
```

Governance rules: `enabled:false` hides but preserves an entry (toggle
back any time); `ignored_patterns` filter regardless of `enabled`;
unrecognised `type` falls back to `systemd`; empty names are skipped.
Config is re-read on every request (FR-11).

### 8.4 API contract

| Endpoint | Returns |
|---|---|
| `GET /api/dashboard` | `{overview, services[], storage[]}` — single call for page load |
| `GET /api/overview` | Host vitals subset |
| `GET /api/services` | Unified service/container list |
| `GET /api/storage` | Drive list |

Record shapes (abridged):

```
overview:  { hostname, model, os, kernel, cpu_pct, cpu_temp_c,
             ram_used_bytes, ram_total_bytes, uptime_seconds, collected_at }

service:   { type: "systemd"|"docker", name, unit, display_name,
             description, state, sub_state, pid, since_ts,
             memory_bytes,                       # systemd
             status_text, image, cpu_pct,        # docker
             memory_used_bytes, memory_limit_bytes }  # docker

storage:   { device, model, size_bytes, is_external, fstype,
             mount, used_bytes, avail_bytes }
```

Failure semantics: if a collector raises, that section degrades to
`unavailable` placeholder records and the rest of the payload is unaffected
(NFR-4). Errors are logged with the logger `pi-dashboard`.

### 8.5 Frontend behaviour

- One HTML page; tabs switch client-side; no routing.
- On load and every 30 s (while `visibilityState === "visible"`): fetch
  `/api/dashboard`, render all three tabs from one payload.
- Connection indicator: green "live" on success, red "offline" on failure
  with the "updated HH:MM:SS" timestamp frozen at last success.
- All dynamic strings are HTML-escaped before insertion.
- Threshold colouring: CPU ≥50 % warm/≥80 % hot; RAM ≥75 %/≥90 %;
  drive usage bars likewise; temperature note flips to "getting warm" ≥70 °C.
- Services table shows type, state pill, PID (systemd only), uptime
  (systemd: computed from `since_ts`; docker: parsed from "Up 3 days"),
  memory, CPU (docker only), description.
- Storage tab: card grid (all drives) plus table (device, mount, type,
  kind, size, used, free, usage % + bar). Dashboard tab shows the first
  four drives as cards.

---

## 9. Security Design

### 9.1 Threat model

Primary asset: the Pi and its container workloads. Considered adversary
paths: exploitation of the dashboard's HTTP surface; compromise of the
service account; lateral movement via Docker; the admin account as
crown-jewel target.

### 9.2 Least-privilege service account

The initial deployment ran the service as the owner's admin user —
identified as a design flaw during review. The corrected design:

- Dedicated system user **`dashsvc`**: no sudo (not in `sudo` group),
  no `docker` group membership, shell `/usr/sbin/nologin`, password locked,
  home/application under `/opt/pi-dashboard`.
- **Critical rationale:** membership in the `docker` group is
  root-equivalent (trivial host-root escape via bind-mounting `/`). The
  dashboard therefore holds *no* direct Docker access at all.
- Docker reach is exclusively via the **socket proxy**: loopback-only
  (`127.0.0.1:2375`), `POST=0` (GET/HEAD only), whitelist limited to the
  API groups the dashboard needs (`CONTAINS`-list state, ping, version).
  The `DOCKER_HOST` environment variable is set only inside the systemd
  unit — nothing else on the host inherits it.
- Documented caveat: an open upstream issue (Tecnativa socket-proxy
  #182/#183, Aug 2026) reports that `CONTAINERS=1` can enable sibling
  GET endpoints (logs/archives) even with `POST=0`; the design therefore
  prefers finer-grained proxy images (linuxserver/wollomatic), pins the
  proxy to loopback, and tracks upstream. Residual risk recorded in the
  design document §7.
- Unit hardening: `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=true`, `PrivateTmp`, `ProtectKernelTunables`,
  `ProtectControlGroups`, `RestrictSUIDSGID`.

### 9.3 Broader hardening posture (adopted guidance)

- Admin account protection is treated separately from service isolation:
  SSH key-only auth, no root login, fail2ban, sudo always with password,
  and no risky interactive software run under the admin account.
- Container fleet hygiene (v1 audit pending, see roadmap): no container
  mounts the Docker socket unless genuinely required; no `--privileged`;
  per-container `no-new-privileges:true` and capability dropping;
  `userns-remap`/rootless Docker identified as the long-term structural
  fix so container escapes do not land on host root.

### 9.4 Verification artefacts

See §11 checklist — notably `sudo -l -U dashsvc` (must deny), `groups
dashsvc` (must not include `docker`), proxied `docker ps` (must succeed),
direct `docker ps` as `dashsvc` (must fail), `docker pull` through the
proxy (must fail with 403).

---

## 10. Deployment Procedure

> Rebuild reference: executes on a fresh Raspberry Pi OS install. Assumes
> project files are available locally and `pi` denotes the admin user;
> substitute the real username throughout.

**Phase 1 — Prepare**
```bash
sudo apt update && sudo apt upgrade -y
python3 --version    # expect 3.9+
```

**Phase 2 — Files**
```bash
mkdir -p ~/pi-dashboard/templates ~/pi-dashboard/static
# copy the 8 project files into the structure per Appendix A
```

**Phase 3 — Python environment**
```bash
cd ~/pi-dashboard
python3 -m venv venv
venv/bin/pip install -r requirements.txt gunicorn
```

**Phase 4 — Configure**
- Edit `config.yaml`: replace the sample service list with the real
  workload (discover names via `systemctl list-units --type=service
  --state=running` and `docker ps --format '{{.Names}}'`).
- Edit `pi-dashboard.service`: set `User=`, `WorkingDirectory=`, and the
  venv `ExecStart=` path to match the deployment (or the hardened variant
  from the low-privilege rollout below).

**Phase 5 — First manual test** (never skip)
```bash
venv/bin/python app.py           # expect: Running on http://0.0.0.0:5000
curl -s http://localhost:5000/api/dashboard | python3 -m json.tool | head -40
```
Browser test from another LAN device. Check permissions suspects if
services show `not-found`.

**Phase 6 — Install as service**
```bash
sudo cp ~/pi-dashboard/pi-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-dashboard
systemctl status pi-dashboard     # want: active (running)
```

**Phase 6b — Low-privilege rollout (recommended final state; see §9)**

1. Create user: `sudo useradd --system --home-dir /opt/pi-dashboard
   --shell /usr/sbin/nologin dashsvc && sudo passwd -l dashsvc`
2. Relocate app to `/opt/pi-dashboard`, `chown -R dashsvc:dashsvc`,
   rebuild venv as that user.
3. Deploy socket proxy:
   ```bash
   sudo docker run -d --name socket-proxy --restart unless-stopped \
     -p 127.0.0.1:2375:2375 \
     -v /var/run/docker.sock:/var/run/docker.sock:ro \
     -e POST=0 -e CONTAINERS=1 -e PING=1 -e VERSION=1 \
     lscr.io/linuxserver/socket-proxy:latest
   ```
4. Install the hardened unit (`User=dashsvc`,
   `Environment=DOCKER_HOST=tcp://127.0.0.1:2375`, sandbox directives),
   `daemon-reload`, restart.

**Phase 7 — Post-install verification** — see §11.

---

## 11. Testing & Verification

### 11.1 Functional checklist (executed during build)

- [x] API returns all three sections with sane values (`/api/dashboard`)
- [x] KPI sanity: idle CPU temp in normal range; RAM % plausible
- [x] All configured services green; deliberate typos render `not-found`
- [x] All drives appear; externals tagged "External"
- [x] Tab switching; Services and Storage tables populate
- [x] 30 s auto-refresh advances the timestamp; polling pauses on hidden tab
- [x] Docker containers show live CPU/RAM; `unavailable` state appears when
      the daemon is down; `not-found` for removed container names
- [x] Config edits (including `enabled:false` flips) apply on next load
- [x] Reboot test: dashboard returns unattended

### 11.2 Security checklist (post 6b rollout)

- [ ] `sudo -l -U dashsvc` → not permitted
- [ ] `groups dashsvc` → only `dashsvc`
- [ ] `getent passwd dashsvc` → `/usr/sbin/nologin`
- [ ] As dashsvc: proxied `docker ps` succeeds; unproxied `docker ps`
      fails; proxied `docker pull` fails (403)
- [ ] `systemctl show pi-dashboard -p User` → `dashsvc`

### 11.3 Incidents found during testing (and fixed)

None during v1 testing

---

## 12. Operations Manual

### 12.1 Routine

| Task | Command / action |
|---|---|
| Service status | `systemctl status pi-dashboard` |
| Application logs | `journalctl -u pi-dashboard -n 50 -f` |
| Change visible services | Edit `config.yaml` → just refresh the browser |
| Restart after rare code change | `sudo systemctl restart pi-dashboard` |
| Update dependencies | `venv/bin/pip install -r requirements.txt gunicorn -U` then restart |

### 12.2 Troubleshooting matrix

| Symptom | Likely cause → action |
|---|---|
| "offline" pill in header | App down or port blocked → `systemctl status`; `journalctl`; `hostname -I`; firewall `sudo ufw allow 5000` if ufw active |
| Service `not-found` despite running | Unit-name mismatch (case matters, e.g. `pihole-FTL`) → copy exact name from `systemctl list-units`; for containers, exact `docker ps` name |
| All containers `unavailable` | Daemon down, missing CLI, or `dashsvc` can't reach proxy → check `socket-proxy` container, `DOCKER_HOST` in unit, proxy reachable on 127.0.0.1:2375 |
| Config edit not visible | Should apply per-request; if truly stuck, restart service |
| Page unstyled | `style.css` missing/misnamed in `static/` → `curl -I http://localhost:5000/static/style.css` |
| Storage oddities/duplicates | snap/loop mounts are excluded by design; unexpected values → compare `curl /api/storage` with raw `lsblk`/`df` |

### 12.3 Backup & restore

The entire system is stateless; backup = the 8 project files + the proxy
container invocation + the unit file. Restore = repeat Phase 3–6 on new
media (minutes). No databases or mutable state exist.

---

## 13. Known Issues & Lessons Learned

**Known limitations (accepted for v1):**

1. No history/graphs — data is ephemeral by design; persistence is a
   roadmap item if ever wanted.
2. No dashboard authentication — acceptable on LAN + read-only; must be
   revisited the moment remote access lands (wg-easy tunnel provides the
   perimeter; re-evaluate then).
3. Docker stats adds ~1–2 s latency to `/api/dashboard` — accepted trade
   for live per-container numbers.
4. Socket-proxy upstream open issue narrows (but does not eliminate)
   the guarantee that only *intended* GET endpoints pass — tracked in §9.

**Lessons learned (process):**

1. Rewrites are risky: a renderer rewrite dropped a function its caller
   still depended on. Mitigation going forward: replace files wholesale
   and re-run the functional checklist after any multi-file change.
2. Deployment path assumptions (file names, usernames) deserve explicit
   placeholders in artifacts, not implicit guesses — placeholders are now
   marked throughout this document.
3. Least privilege is a *design-time* decision: bolting `docker` group
   membership onto a service account would have silently preserved
   root-equivalence. The socket-proxy pattern was chosen deliberately.

---

## 14. Future Roadmap

Owner-approved v1 freeze; items sequenced by dependency and value.

### Phase 2 — Remote access (wg-easy)

- Deploy wg-easy container on the Pi; forward a single UDP port (51820);
  establish DDNS name.
- Issue per-member configs/QR codes; document revocation procedure.
- Revisit auth necessity once the tunnel perimeter exists.
- Firewall pass: Pi-local `ufw` allowing LAN + tunnel interface only.

### Phase 2.5 — Security completion

- Execute container fleet audit (§9.3): socket mounts, privileged flags,
  capability drops, `no-new-privileges`.
- SSH hardening + fail2ban on the admin account.
- Track socket-proxy upstream issue; adopt finer-grained proxy config when
  available.

### Phase 3 — Feature backlog (cheap wins first)

- Container health-status rendering (independent of running state).
- Restart-count display (silent-failure detector).
- Published ports on cards ("what URL is this?").
- Compose-project grouping via `com.docker.compose.project` label
  (owner deferred grouping in v1; data source already understood).
- Per-directory disk breakdown; storage growth tracking.
- Event-driven notifications (Docker events / systemd failures) replacing
  or augmenting the poll — changes architecture from poll to subscribe;
  assess Pi cost first.

### Phase 4 — Structural evolution (calm-weekend items)

- `userns-remap` for the Docker daemon, then evaluate full **rootless
  Docker** (or Podman) so container escapes stop terminating at host root.
- Consider history persistence (round-robin file or SQLite) only if
  graphing becomes a genuine want.

---

## 15. Appendices

### Appendix A — File inventory & layout

```
/opt/pi-dashboard/                (or ~/pi-dashboard pre-hardening)
├── app.py                        Flask routes, config load, unification
├── collectors.py                 all data acquisition
├── config.yaml                   service allowlist + patterns
├── requirements.txt              Flask, PyYAML (gunicorn installed with it)
├── pi-dashboard.service          systemd unit (hardened variant)
├── venv/                         project virtualenv
├── templates/
│   └── index.html                single-page UI
└── static/
    ├── style.css                 theme/layout
    └── app.js                    render + poll logic
```

### Appendix B — Complete API response example (illustrative)

```json
{
  "overview": {
    "hostname": "pi-server",
    "model": "Raspberry Pi 5 Model B Rev 1.0",
    "os": "Debian GNU/Linux 12 (bookworm)",
    "kernel": "6.6.x",
    "cpu_pct": 12.5,
    "cpu_temp_c": 52.3,
    "ram_used_bytes": 1987000000,
    "ram_total_bytes": 8300000000,
    "uptime_seconds": 1209600,
    "collected_at": 1763734000
  },
  "services": [
    { "type": "systemd", "name": "sshd", "unit": "ssh.service",
      "display_name": "SSH Server", "state": "active",
      "sub_state": "running", "pid": 612, "since_ts": 1762600000,
      "memory_bytes": 15800000, "description": "OpenBSD Secure Shell server" },
    { "type": "docker", "name": "pihole", "unit": "pihole",
      "display_name": "Pi-hole", "state": "active",
      "description": "Up 3 weeks", "image": "pihole/pihole:latest",
      "cpu_pct": 0.42, "memory_used_bytes": 56000000,
      "memory_limit_bytes": 2088000000 }
  ],
  "storage": [
    { "device": "/dev/mmcblk0p2", "model": null, "size_bytes": 128000000000,
      "is_external": false, "fstype": "ext4", "mount": "/",
      "used_bytes": 48000000000, "avail_bytes": 74000000000 },
    { "device": "/dev/sda1", "model": "Portable SSD", "size_bytes": 1000000000000,
      "is_external": true, "fstype": "exfat", "mount": "/mnt/media",
      "used_bytes": 310000000000, "avail_bytes": 690000000000 }
  ]
}
```
*(Values illustrative; schema is authoritative.)*

### Appendix C — Configuration reference

| Key | Applies to | Effect |
|---|---|---|
| `services[].name` | both | systemd unit name (suffix optional) or exact container name |
| `services[].type` | both | `systemd` (default) or `docker` |
| `services[].display_name` | both | friendly label; defaults to name |
| `services[].enabled` | both | `false` hides entry, config preserved |
| `ignored_patterns[]` | both | case-insensitive glob (`*`, `?`) on `name`; overrides `enabled` |

### Appendix D — Glossary

| Term | Meaning |
|---|---|
| WSGI | Python standard interface between web servers and applications; gunicorn speaks it to serve Flask |
| Socket proxy | Filtering gateway in front of the Docker socket permitting only whitelisted read-only API paths |
| `dashsvc` | Dedicated unprivileged service account created for this dashboard |
| DDNS | Dynamic DNS mapping a fixed name to a changing home IP |
| wg-easy | Self-hosted WireGuard management UI providing per-peer tunnels and QR provisioning |
| Peers | WireGuard clients (e.g. family members' devices) holding issued keys |
| RM flag | lsblk attribute marking removable/external media |
| Unified view | Single Services list combining systemd units and Docker containers with type badges |

---

*End of document. Version 1.0 — released 21 Nov 2026.*
