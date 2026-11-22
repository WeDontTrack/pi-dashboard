/* app.js - rendering + polling for the read-only Pi dashboard */

const POLL_INTERVAL_MS = 30000;

// ------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);

function fmtBytes(b) {
  if (b == null || isNaN(b)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = b;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtUptime(seconds) {
  if (seconds == null || isNaN(seconds)) return "—";
  if (seconds < 0) seconds = 0;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function fmtTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "—";
}

function fmtCpu(pct) {
  return pct == null ? "—" : pct + "%";
}

// maps API service state -> dot class + pill class + label
const STATE_MAP = {
  active:      { dot: "dot-active", pill: "pill-ok",   label: "running" },
  activating:  { dot: "dot-warn",   pill: "pill-warn", label: "starting" },
  reloading:   { dot: "dot-warn",   pill: "pill-warn", label: "reloading" },
  degraded:    { dot: "dot-warn",   pill: "pill-warn", label: "degraded" },
  failed:      { dot: "dot-error",  pill: "pill-err",  label: "failed" },
  "not-found": { dot: "dot-error",  pill: "pill-err",  label: "not found" },
  unavailable: { dot: "dot-error",  pill: "pill-err",  label: "unavailable" },
  inactive:    { dot: "dot-idle",   pill: "pill-warn", label: "stopped" },
};
const stateInfo = (s) =>
  STATE_MAP[s] || { dot: "dot-idle", pill: "pill-warn", label: s || "unknown" };

function esc(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// ------------------------------------------------------------- tabs

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ------------------------------------------------------------- rendering

function renderOverview(o) {
  $("hostname").textContent = o.hostname || "Pi Dashboard";
  $("foot-os").textContent = [o.model, o.os].filter(Boolean).join(" · ");

  const cpu = o.cpu_pct;
  $("kpi-cpu").textContent = cpu == null ? "—" : cpu + "%";
  const cpuBar = $("kpi-cpu-bar");
  cpuBar.style.width = (cpu ?? 0) + "%";
  cpuBar.className =
    "bar-fill " + (cpu >= 80 ? "hot" : cpu >= 50 ? "warm" : "");

  $("kpi-temp").textContent = o.cpu_temp_c == null ? "—" : o.cpu_temp_c + "°C";
  $("kpi-temp-note").textContent =
    o.cpu_temp_c >= 70 ? "getting warm" : "SoC thermal zone";

  const pctRam = o.ram_total_bytes
    ? Math.round((100 * o.ram_used_bytes) / o.ram_total_bytes)
    : null;
  $("kpi-ram").textContent = pctRam == null ? "—" : pctRam + "%";
  const ramBar = $("kpi-ram-bar");
  ramBar.style.width = (pctRam ?? 0) + "%";
  ramBar.className = "bar-fill " + (pctRam >= 90 ? "hot" : pctRam >= 75 ? "warm" : "");
  $("kpi-ram-sub").textContent =
    `${fmtBytes(o.ram_used_bytes)} of ${fmtBytes(o.ram_total_bytes)} used`;

  $("kpi-uptime").textContent = fmtUptime(o.uptime_seconds);
  $("kpi-model").textContent = o.model || "";
}

function usageBar(pct) {
  const cls = pct >= 90 ? " hot" : pct >= 75 ? " warm" : "";
  return `<div class="bar"><div class="bar-fill${cls}" style="width:${pct}%"></div></div>`;
}

function serviceCard(svc) {
  const st = stateInfo(svc.state);
  const isDocker = svc.type === "docker";
  const stats = isDocker
    ? `<div class="kv"><span class="k">CPU</span><span>${fmtCpu(svc.cpu_pct)}</span></div>
       <div class="kv"><span class="k">Memory</span><span>${fmtBytes(svc.memory_used_bytes)}${svc.memory_limit_bytes ? " / " + fmtBytes(svc.memory_limit_bytes) : ""}</span></div>`
    : `<div class="kv"><span class="k">Memory</span><span>${fmtBytes(svc.memory_bytes)}</span></div>
       <div class="kv"><span class="k">Since</span><span>${fmtTime(svc.since_ts)}</span></div>`;
  return `
    <div class="card">
      <div class="card-head">
        <span class="status-dot ${st.dot}"></span>
        <span class="card-title">${esc(svc.display_name)}</span>
        <span class="pill ${isDocker ? "pill-blue" : "pill-grey"}">${isDocker ? "docker" : "systemd"}</span>
      </div>
      <div class="card-sub">${esc(svc.unit)} · <span class="pill ${st.pill}">${st.label}</span></div>
      ${stats}
    </div>`;
}

function driveCard(d) {
  const pct = d.size_bytes ? Math.round((100 * d.used_bytes) / d.size_bytes) : 0;
  const kind = d.is_external ? "External" : "Internal";
  return `
    <div class="card">
      <div class="card-head">
        <span class="card-title">${esc(d.mount)}</span>
        <span class="pill ${d.is_external ? "pill-warn" : "pill-ok"}">${kind}</span>
      </div>
      <div class="card-sub">${esc(d.device)}${d.fstype ? " · " + esc(d.fstype) : ""}</div>
      ${usageBar(pct)}
      <div class="kv"><span class="k">Used</span><span>${fmtBytes(d.used_bytes)} / ${fmtBytes(d.size_bytes)}</span></div>
      <div class="kv"><span class="k">Free</span><span>${fmtBytes(d.avail_bytes)}</span></div>
      ${d.model ? `<div class="kv"><span class="k">Model</span><span>${esc(d.model)}</span></div>` : ""}
    </div>`;
}

function renderServices(list) {
  // dashboard cards
  $("dash-services").innerHTML = list.map(serviceCard).join("") ||
    `<div class="card"><div class="card-sub">No services enabled in config.yaml</div></div>`;

  // services table
  $("svc-tbody").innerHTML = list.map((svc) => {
    const st = stateInfo(svc.state);
    const isDocker = svc.type === "docker";
    let uptime = "—";
    if (!isDocker && svc.since_ts) {
      uptime = fmtUptime(Date.now() / 1000 - svc.since_ts);
    } else if (isDocker && svc.status_text) {
      // docker "Up 3 days" / "Up 2 hours" etc.
      uptime = svc.status_text.replace(/^Up\s*/i, "");
    }
    const mem = isDocker
      ? `${fmtBytes(svc.memory_used_bytes)}${svc.memory_limit_bytes ? " / " + fmtBytes(svc.memory_limit_bytes) : ""}`
      : fmtBytes(svc.memory_bytes);
    return `<tr>
      <td><span class="status-dot ${st.dot}"></span></td>
      <td><strong>${esc(svc.display_name)}</strong></td>
      <td>${isDocker ? "docker" : "systemd"}</td>
      <td><span class="pill ${st.pill}">${st.label}</span></td>
      <td>${svc.pid ?? "—"}</td>
      <td>${uptime}</td>
      <td>${mem}</td>
      <td>${fmtCpu(isDocker ? svc.cpu_pct : null)}</td>
      <td class="card-sub">${esc(svc.description)}</td>
    </tr>`;
  }).join("");
}

function renderStorage(list) {
  $("dash-storage").innerHTML = list.slice(0, 4).map(driveCard).join("") ||
    `<div class="card"><div class="card-sub">No mounted drives reported</div></div>`;

  $("store-cards").innerHTML = list.map(driveCard).join("");

  $("store-tbody").innerHTML = list.map((d) => {
    const pct = d.size_bytes ? Math.round((100 * d.used_bytes) / d.size_bytes) : 0;
    return `<tr>
      <td>${esc(d.device)}</td>
      <td><strong>${esc(d.mount)}</strong></td>
      <td>${esc(d.fstype || "—")}</td>
      <td>${d.is_external ? "External" : "Internal"}</td>
      <td>${fmtBytes(d.size_bytes)}</td>
      <td>${fmtBytes(d.used_bytes)}</td>
      <td>${fmtBytes(d.avail_bytes)}</td>
      <td class="usage-cell">${pct}% ${usageBar(pct)}</td>
    </tr>`;
  }).join("");
}

// ------------------------------------------------------------- polling

let timer = null;

async function refresh() {
  try {
    const res = await fetch("/api/dashboard");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    renderOverview(data.overview);
    renderServices(data.services);
    renderStorage(data.storage);

    $("updated-at").textContent = "updated " + new Date().toLocaleTimeString();
    $("conn").textContent = "live";
    $("conn").className = "pill pill-ok";
  } catch (err) {
    console.error("refresh failed:", err);
    $("conn").textContent = "offline";
    $("conn").className = "pill pill-err";
  }
}

function schedule() {
  clearInterval(timer);
  timer = setInterval(() => {
    if (document.visibilityState === "visible") refresh();
  }, POLL_INTERVAL_MS);
}

refresh();
schedule();
