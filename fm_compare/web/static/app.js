"use strict";

const state = { jobId: null, resolutions: [], hasV3: false };

let _chatHistory = [];

const $ = (id) => document.getElementById(id);

function showError(msg) {
  $("errorArea").innerHTML = `<div class="error-box">${escapeHtml(msg)}</div>`;
}
function clearError() { $("errorArea").innerHTML = ""; }

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function enableCard(id) { $(id).classList.remove("disabled"); }
function disableCard(id) { $(id).classList.add("disabled"); }

async function apiJson(url, opts) {
  const resp = await fetch(url, opts);
  if (resp.status === 401) { location.href = "/login"; throw new Error("unauthorized"); }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* non-json */ }
  if (!resp.ok) {
    const detail = data && (data.detail || data.error) ? (data.detail || data.error) : `Ошибка ${resp.status}`;
    throw new Error(detail);
  }
  return data;
}

// ── Step 1: upload ──────────────────────────────────────────────────────────
function updateUploadBtn() {
  $("uploadBtn").disabled = !($("fileV1").files[0] && $("fileV2").files[0]);
}
$("fileV1").addEventListener("change", updateUploadBtn);
$("fileV2").addEventListener("change", updateUploadBtn);

$("uploadBtn").addEventListener("click", async () => {
  clearError();
  $("uploadBtn").disabled = true;
  $("uploadBtn").textContent = "Загрузка…";
  try {
    const fd = new FormData();
    fd.append("v1", $("fileV1").files[0]);
    fd.append("v2", $("fileV2").files[0]);
    if ($("fileV3").files[0]) fd.append("v3", $("fileV3").files[0]);
    const data = await apiJson("/api/upload", { method: "POST", body: fd });
    state.jobId = data.job_id;
    state.hasV3 = !!data.has_v3;
    renderSheets("sheetsV1", data.sheets_v1);
    renderSheets("sheetsV2", data.sheets_v2);
    if (state.hasV3) {
      renderSheets("sheetsV3", data.sheets_v3 || []);
      $("sheetsV3Wrap").style.display = "";
    } else {
      $("sheetsV3Wrap").style.display = "none";
    }
    enableCard("card-sheets");
  } catch (e) {
    showError(e.message);
  } finally {
    $("uploadBtn").disabled = false;
    $("uploadBtn").textContent = "Загрузить и прочитать листы";
  }
});

function renderSheets(containerId, sheets) {
  const c = $(containerId);
  c.innerHTML = "";
  sheets.forEach((name) => {
    const lbl = document.createElement("label");
    lbl.innerHTML = `<input type="checkbox" value="${escapeHtml(name)}"> ${escapeHtml(name)}`;
    c.appendChild(lbl);
  });
}
function selectedSheets(containerId) {
  return Array.from($(containerId).querySelectorAll("input:checked")).map((i) => i.value);
}

// ── Step 2: KPI preview ──────────────────────────────────────────────────────
$("previewBtn").addEventListener("click", async () => {
  clearError();
  const sv1 = selectedSheets("sheetsV1");
  const sv2 = selectedSheets("sheetsV2");
  const sv3 = state.hasV3 ? selectedSheets("sheetsV3") : [];
  if (!sv1.length || !sv2.length) { showError("Выберите хотя бы один лист в каждой версии."); return; }
  $("previewBtn").disabled = true;
  $("previewBtn").textContent = "Анализ…";
  try {
    const data = await apiJson(`/api/${state.jobId}/resolve-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sheets_v1: sv1, sheets_v2: sv2, sheets_v3: sv3 }),
    });
    state.resolutions = data.resolutions;
    renderKpiTable(data.resolutions);
    enableCard("card-kpi");
    enableCard("card-run");
    loadDashboard();
  } catch (e) {
    showError(e.message);
  } finally {
    $("previewBtn").disabled = false;
    $("previewBtn").textContent = "Определить KPI";
  }
});

function renderKpiTable(rows) {
  const tb = $("kpiTable").querySelector("tbody");
  tb.innerHTML = "";
  rows.forEach((r, i) => {
    const tr = document.createElement("tr");
    tr.className = (r.addr_v1 && r.addr_v2)
      ? (r.source === "llm" ? "found ai-found" : "found")
      : "missing";
    const v3Cell = state.hasV3
      ? `<td class="v3-col"><input type="text" data-i="${i}" data-f="addr_v3" value="${escapeHtml(r.addr_v3 || '')}" style="width:110px"></td>`
      : `<td class="v3-col"></td>`;
    tr.innerHTML = `
      <td>${escapeHtml(r.kpi_name)}</td>
      <td>${escapeHtml(r.kpi_group)}</td>
      <td>${escapeHtml(r.label_v1)}</td>
      <td><input type="text" data-i="${i}" data-f="addr_v1" value="${escapeHtml(r.addr_v1)}"></td>
      <td><input type="text" data-i="${i}" data-f="unit_v1" value="${escapeHtml(r.unit_v1)}" style="width:70px"></td>
      <td>${escapeHtml(r.label_v2)}</td>
      <td><input type="text" data-i="${i}" data-f="addr_v2" value="${escapeHtml(r.addr_v2)}"></td>
      <td><input type="text" data-i="${i}" data-f="unit_v2" value="${escapeHtml(r.unit_v2)}" style="width:70px"></td>
      ${v3Cell}`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("change", (e) => {
      const { i, f } = e.target.dataset;
      state.resolutions[i][f] = e.target.value;
      if (f === "addr_v1" || f === "addr_v2") {
        const r = state.resolutions[i];
        e.target.closest("tr").className = (r.addr_v1 && r.addr_v2) ? "found" : "missing";
      }
    });
  });
}

// ── Step 3: run + poll ───────────────────────────────────────────────────────
$("runBtn").addEventListener("click", async () => {
  clearError();
  $("runBtn").disabled = true;
  $("progressWrap").style.display = "block";
  $("card-results").style.display = "none";
  if (state.hasV3) {
    $("v2v3StatusWrap").style.display = "block";
    $("progressBarV2V3").style.width = "0%";
    $("statusMsgV2V3").textContent = "Ожидание…";
  }
  try {
    const body = {
      resolutions: state.resolutions,
      mode: $("mode").value,
      top_x: parseInt($("topX").value, 10) || 10,
      materiality_abs: floatOrNull($("matAbs").value),
      materiality_pct: floatOrNull($("matPct").value),
    };
    await apiJson(`/api/${state.jobId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    pollStatus();
    if (state.hasV3) pollStatus2();
  } catch (e) {
    showError(e.message);
    $("runBtn").disabled = false;
  }
});

function floatOrNull(v) { const n = parseFloat(v); return isNaN(n) ? null : n; }

async function pollStatus() {
  try {
    const s = await apiJson(`/api/${state.jobId}/status`);
    $("progressBar").style.width = (s.progress || 0) + "%";
    $("statusMsg").textContent = s.message || "";
    if (s.status === "done") { await loadSummary(); $("runBtn").disabled = false; return; }
    if (s.status === "error") { showError(s.error || "Сравнение завершилось с ошибкой."); $("runBtn").disabled = false; return; }
    setTimeout(pollStatus, 800);
  } catch (e) {
    showError(e.message);
    $("runBtn").disabled = false;
  }
}

async function pollStatus2() {
  try {
    const s = await apiJson(`/api/${state.jobId}/status2`);
    $("progressBarV2V3").style.width = (s.progress || 0) + "%";
    $("statusMsgV2V3").textContent = s.message || "";
    if (s.status === "done") {
      $("statusMsgV2V3").textContent = "Готово.";
      $("downloadBtn2").style.display = "";
      return;
    }
    if (s.status === "error") {
      $("statusMsgV2V3").textContent = "Ошибка: " + (s.error || "неизвестная ошибка");
      return;
    }
    setTimeout(pollStatus2, 800);
  } catch (e) {
    $("statusMsgV2V3").textContent = "Ошибка опроса: " + escapeHtml(e.message);
  }
}

// ── Results ──────────────────────────────────────────────────────────────────
async function loadSummary() {
  const data = await apiJson(`/api/${state.jobId}/summary`);
  const c = data.counts || {};
  $("counts").textContent =
    `Режим: ${data.mode === "quick" ? "Быстрая проверка" : "Полный аудит"} · ` +
    `Diff: ${c.diff_rows} · KPI: ${c.kpi_values} · Формул: ${c.formula_changes} · ` +
    `Шифтов: ${c.timing_shifts} · Предупреждений: ${c.warnings}`;
  renderBlocks(data.summary_blocks || []);
  $("card-results").style.display = "block";
  $("card-results").scrollIntoView({ behavior: "smooth" });
}

function renderBlocks(blocks) {
  const wrap = $("summaryBlocks");
  wrap.innerHTML = "";
  blocks.forEach((b) => {
    const div = document.createElement("div");
    div.className = "block " + (b.type || "");
    let html = `<h3>${escapeHtml(b.title || "")}</h3>`;
    if (b.text) html += `<div>${escapeHtml(b.text)}</div>`;
    if (b.items && b.items.length) {
      html += "<ul>" + b.items.map((it) => `<li>${styleItem(it)}</li>`).join("") + "</ul>";
    }
    div.innerHTML = html;
    wrap.appendChild(div);
  });
}

function styleItem(it) {
  const s = escapeHtml(it);
  if (s.startsWith("▼")) return `<span class="down">${s}</span>`;
  if (s.startsWith("▲")) return `<span class="up">${s}</span>`;
  if (s.includes("⚠")) return `<span class="warn">${s}</span>`;
  return s;
}

$("downloadBtn").addEventListener("click", () => {
  window.location.href = `/api/${state.jobId}/report.xlsx`;
});

$("downloadBtn2").addEventListener("click", () => {
  window.location.href = `/api/${state.jobId}/report2.xlsx`;
});

$("logoutBtn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
});

// ── Dashboard ─────────────────────────────────────────────────────────────────

let _dashResolutions = [];   // current resolution state for POST re-calc

async function loadDashboard() {
  try {
    const data = await apiJson(`/api/${state.jobId}/dashboard`);
    _dashResolutions = (data.resolutions || []).map(r => Object.assign({}, r));
    renderDashboard(data);
    enableCard("card-dashboard");
    initSensitivity(_dashResolutions);
  } catch (e) {
    showError("Не удалось загрузить дашборд: " + (e.message || String(e)));
  }
}

function renderDiscrepancies(discrepancies) {
  const el = $("dashDiscrepancies");
  if (!discrepancies || !discrepancies.length) { el.style.display = "none"; return; }
  el.style.display = "";
  const rows = discrepancies.map(d => {
    const cls = d.severity === "high" ? "disc-high" : "disc-medium";
    return `<div class="disc-row ${cls}"><span class="disc-article">${escapeHtml(d.article)}</span><span class="disc-msg">${escapeHtml(d.message)}</span></div>`;
  }).join("");
  el.innerHTML = `<div class="disc-header">Расхождения между листами (${discrepancies.length})</div>${rows}`;
}

function fmtNum(v, unit) {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  if (unit && unit.includes("%")) return n.toFixed(2) + " %";
  const a = Math.abs(n);
  if (a >= 1e9) return sign(n) + (a / 1e9).toFixed(2) + " млрд";
  if (a >= 1e6) return sign(n) + (a / 1e6).toFixed(1) + " млн";
  if (a >= 1e3) return sign(n) + (a / 1e3).toFixed(0) + " тыс";
  return n.toFixed(2);
}
function sign(n) { return n < 0 ? "−" : ""; }

function fmtDelta(d, unit) {
  if (d == null || !isFinite(Number(d))) return "—";
  const n = Number(d);
  const arrow = n > 0 ? "▲ " : n < 0 ? "▼ " : "= ";
  return arrow + fmtNum(Math.abs(n), unit);
}

function renderDashboard(data) {
  const kpis = data.kpis || [];
  const hasV3 = !!data.has_v3;
  const dv1 = (data.date_v1 || "").slice(0, 10);
  const dv2 = (data.date_v2 || "").slice(0, 10);
  const dv3 = (data.date_v3 || "").slice(0, 10);

  $("badgeV1").textContent = dv1;
  $("badgeV2").textContent = dv2;
  $("badgeV3").textContent = dv3;

  const table = $("dashTable");
  if (hasV3) table.classList.add("dash-v3"); else table.classList.remove("dash-v3");

  let datesHtml = `<span>V1 (новее): <strong>${escapeHtml(dv1 || "—")}</strong></span>` +
    `<span>V2: <strong>${escapeHtml(dv2 || "—")}</strong></span>`;
  if (hasV3) datesHtml += `<span>V3 (база): <strong>${escapeHtml(dv3 || "—")}</strong></span>`;
  $("dashDates").innerHTML = datesHtml;

  renderDiscrepancies(data.discrepancies || []);

  const tb = table.querySelector("tbody");
  tb.innerHTML = "";
  let lastGroup = null;
  const colspan = hasV3 ? 7 : 5;

  kpis.forEach((k) => {
    if (k.kpi_group && k.kpi_group !== lastGroup) {
      lastGroup = k.kpi_group;
      const hdr = document.createElement("tr");
      hdr.className = "dash-group";
      hdr.innerHTML = `<td colspan="${colspan}">${escapeHtml(k.kpi_group)}</td>`;
      tb.appendChild(hdr);
    }

    const res = _dashResolutions.find(r => r.kpi_name === k.kpi_name) || {};
    const addrV1 = res.addr_v1 || "";
    const addrV2 = res.addr_v2 || "";
    const addrV3 = res.addr_v3 || "";
    const chipCls = res.source === "llm" ? "addr-chip llm-corrected" : "addr-chip";
    const dirClass = k.direction === "up" ? "up" : k.direction === "down" ? "down" : "";
    const dir23 = k.delta_v2_v3 != null ? (k.delta_v2_v3 > 0 ? "up" : k.delta_v2_v3 < 0 ? "down" : "") : "";
    const pct = k.delta_pct != null && isFinite(k.delta_pct)
      ? (k.delta_pct > 0 ? "+" : "") + k.delta_pct.toFixed(1) + " %" : "—";

    const tr = document.createElement("tr");
    tr.className = "dash-kpi";
    tr.dataset.kpi = k.kpi_name;
    tr.innerHTML = `
      <td class="col-kpi">
        <div class="kpi-label">${escapeHtml(k.kpi_name)}</div>
        <div class="addr-chips">
          <span class="${chipCls}" data-kpi="${escapeHtml(k.kpi_name)}" data-ver="v2"><span class="chip-ver">V2</span>${escapeHtml(addrV2) || "—"}</span>
          <span class="${chipCls}" data-kpi="${escapeHtml(k.kpi_name)}" data-ver="v1"><span class="chip-ver">V1</span>${escapeHtml(addrV1) || "—"}</span>
          ${state.hasV3 ? `<span class="${chipCls}" data-kpi="${escapeHtml(k.kpi_name)}" data-ver="v3"><span class="chip-ver">V3</span>${escapeHtml(addrV3) || "—"}</span>` : ""}
        </div>
      </td>
      <td class="col-v3 v3-col num">${fmtNum(k.value_v3, k.unit)}</td>
      <td class="col-delta23 v3-col ${dir23}">${fmtDelta(k.delta_v2_v3, k.unit)}</td>
      <td class="col-v2 num">${fmtNum(k.value_v2, k.unit)}</td>
      <td class="col-delta ${dirClass}">${fmtDelta(k.delta, k.unit)}</td>
      <td class="col-pct ${dirClass}">${pct}</td>
      <td class="col-v1 num">${fmtNum(k.value_v1, k.unit)}</td>`;
    tb.appendChild(tr);
  });

  // Wire address chips
  tb.querySelectorAll(".addr-chip").forEach(chip => {
    chip.addEventListener("click", () => startAddrEdit(chip));
  });

  enableChatPanel();
}

function startAddrEdit(chip) {
  if (chip.querySelector("input")) return;
  const kpiName = chip.dataset.kpi;
  const ver = chip.dataset.ver;
  const verLabel = chip.querySelector(".chip-ver");
  const verText = verLabel ? verLabel.textContent : "";
  const cur = chip.textContent.replace(verText, "").replace("—", "").trim();

  chip.classList.add("editing");
  chip.innerHTML = "";

  const inp = document.createElement("input");
  inp.type = "text";
  inp.className = "addr-input";
  inp.value = cur;
  inp.placeholder = "Лист!H42";
  chip.appendChild(inp);
  inp.focus();
  inp.select();

  const finish = async () => {
    const newAddr = inp.value.trim();
    chip.classList.remove("editing");
    // Rebuild chip text
    chip.innerHTML = `<span class="chip-ver">${escapeHtml(verText)}</span>${escapeHtml(newAddr) || "—"}`;
    chip.addEventListener("click", () => startAddrEdit(chip));

    if (newAddr === cur) return;
    const res = _dashResolutions.find(r => r.kpi_name === kpiName);
    const fld = ver === "v1" ? "addr_v1" : ver === "v3" ? "addr_v3" : "addr_v2";
    if (res) res[fld] = newAddr;
    else _dashResolutions.push({ kpi_name: kpiName, addr_v1: "", addr_v2: "", addr_v3: "", [fld]: newAddr });

    await refreshDashboard();
  };

  inp.addEventListener("blur", finish);
  inp.addEventListener("keydown", e => {
    if (e.key === "Enter") inp.blur();
    if (e.key === "Escape") {
      chip.classList.remove("editing");
      chip.innerHTML = `<span class="chip-ver">${escapeHtml(verText)}</span>${escapeHtml(cur) || "—"}`;
      chip.addEventListener("click", () => startAddrEdit(chip));
    }
  });
}

async function refreshDashboard() {
  const wrap = $("dashTable").closest(".dash-wrap");
  wrap.classList.add("dash-refreshing");
  try {
    const data = await apiJson(`/api/${state.jobId}/dashboard`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolutions: _dashResolutions }),
    });
    _dashResolutions = (data.resolutions || _dashResolutions).map(r => Object.assign({}, r));
    renderDashboard(data);
  } catch (e) {
    showError("Ошибка обновления дашборда: " + escapeHtml(e.message));
  } finally {
    wrap.classList.remove("dash-refreshing");
  }
}

// ── Sensitivity analysis ───────────────────────────────────────────────────

let _sensPollTimer = null;

function initSensitivity(resolutions) {
  // Pre-populate KPI list from dashboard resolutions (V1 addresses)
  const list = $("sensKpiList");
  list.innerHTML = "";
  (resolutions || []).forEach(r => {
    const addr = r.addr_v1 || "";
    if (!addr) return;
    const id = "sens-kpi-" + Math.random().toString(36).slice(2);
    const item = document.createElement("label");
    item.className = "sens-kpi-item";
    item.innerHTML =
      `<input type="checkbox" id="${id}" checked>` +
      `<span>${escapeHtml(r.kpi_name)}</span>` +
      `<input type="text" class="sens-kpi-addr" data-kpi="${escapeHtml(r.kpi_name)}" ` +
      `value="${escapeHtml(addr)}" placeholder="Лист!A1">`;
    list.appendChild(item);
  });
  enableCard("card-sensitivity");
}

function sensAddInputRow() {
  const tbody = $("sensInputsBody");
  const tr = document.createElement("tr");
  tr.innerHTML =
    `<td><input type="text" placeholder="Цена реализации" style="width:150px"></td>` +
    `<td><input type="text" placeholder="PRICE!B5" style="width:110px"></td>` +
    `<td><input type="text" placeholder="руб./кв.м" style="width:80px"></td>` +
    `<td><input type="number" placeholder="150000" style="width:100px"></td>` +
    `<td><input type="text" placeholder="120000,135000,150000,165000,180000" style="width:260px"></td>` +
    `<td><button class="sens-del-btn" onclick="this.closest('tr').remove()">✕</button></td>`;
  tbody.appendChild(tr);
}

function sensBuildInputs() {
  return Array.from($("sensInputsBody").querySelectorAll("tr")).map(tr => {
    const inputs = tr.querySelectorAll("input");
    const vals = (inputs[4].value || "").split(",").map(s => s.trim()).filter(Boolean).map(Number).filter(v => !isNaN(v));
    return {
      name: inputs[0].value.trim() || inputs[1].value.trim(),
      addr: inputs[1].value.trim(),
      unit: inputs[2].value.trim(),
      base_value: parseFloat(inputs[3].value) || 0,
      values: vals,
    };
  }).filter(r => r.addr && r.values.length);
}

function sensBuildKpiAddrs() {
  return Array.from($("sensKpiList").querySelectorAll("label.sens-kpi-item")).filter(lbl => {
    return lbl.querySelector("input[type=checkbox]").checked;
  }).map(lbl => {
    const addrInp = lbl.querySelector(".sens-kpi-addr");
    return { name: addrInp.dataset.kpi, addr: addrInp.value.trim() };
  }).filter(r => r.addr);
}

$("sensAddInputBtn").addEventListener("click", sensAddInputRow);

$("sensSuggestBtn").addEventListener("click", async () => {
  const btn = $("sensSuggestBtn");
  btn.disabled = true;
  btn.textContent = "Ищем…";
  try {
    const data = await apiJson(`/api/${state.jobId}/suggest-sensitivity-inputs`);
    const candidates = data.candidates || [];
    if (!candidates.length) {
      $("sensMsgArea").textContent = "Параметры-кандидаты не найдены в файле V1.";
      return;
    }
    const tbody = $("sensInputsBody");
    candidates.forEach(c => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><input type="text" value="${escapeHtml(c.name)}" style="width:150px"></td>` +
        `<td><input type="text" value="${escapeHtml(c.addr)}" style="width:110px"></td>` +
        `<td><input type="text" value="${escapeHtml(c.unit)}" style="width:80px"></td>` +
        `<td><input type="number" value="${escapeHtml(String(c.base_value))}" style="width:100px"></td>` +
        `<td><input type="text" placeholder="знач1,знач2,…" style="width:260px"></td>` +
        `<td><button class="sens-del-btn" onclick="this.closest('tr').remove()">✕</button></td>`;
      tbody.appendChild(tr);
    });
    $("sensMsgArea").textContent = `Добавлено ${candidates.length} кандидатов. Укажите значения для перебора.`;
  } catch (e) {
    $("sensMsgArea").textContent = "Ошибка поиска: " + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "✦ Найти авто";
  }
});

$("sensRunBtn").addEventListener("click", async () => {
  clearError();
  const inputs = sensBuildInputs();
  const kpiAddrs = sensBuildKpiAddrs();
  if (!inputs.length) { $("sensMsgArea").textContent = "Добавьте входные параметры."; return; }
  if (!kpiAddrs.length) { $("sensMsgArea").textContent = "Выберите хотя бы один KPI."; return; }

  $("sensRunBtn").disabled = true;
  $("sensMsgArea").textContent = "Запуск…";
  $("sensResults").style.display = "none";

  try {
    await apiJson(`/api/${state.jobId}/sensitivity`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ inputs, kpi_addrs: kpiAddrs, timeout: 120 }),
    });
    $("sensMsgArea").textContent = "Считаем сценарии…";
    _sensPollTimer = setTimeout(sensPoll, 2000);
  } catch (e) {
    $("sensMsgArea").textContent = "Ошибка: " + e.message;
    $("sensRunBtn").disabled = false;
  }
});

async function sensPoll() {
  try {
    const data = await apiJson(`/api/${state.jobId}/sensitivity`);
    if (data.status === "running") {
      $("sensMsgArea").textContent = "Считаем сценарии…";
      _sensPollTimer = setTimeout(sensPoll, 3000);
      return;
    }
    $("sensRunBtn").disabled = false;
    if (data.status === "error") {
      $("sensMsgArea").textContent = "Ошибка: " + (data.error || "неизвестная ошибка");
      return;
    }
    if (data.status === "done") {
      $("sensMsgArea").textContent = "Готово.";
      sensRenderResults(data);
    }
  } catch (e) {
    $("sensMsgArea").textContent = "Ошибка опроса: " + e.message;
    $("sensRunBtn").disabled = false;
  }
}

// ── AI Chat panel ─────────────────────────────────────────────────────────────

function enableChatPanel() {
  enableCard("card-chat");
  _chatHistory = [];
  $("chatMessages").innerHTML = "";
}

function appendChatMsg(role, text, streaming) {
  const div = document.createElement("div");
  div.className = "chat-msg " + role + (streaming ? " streaming" : "");
  div.textContent = text;
  $("chatMessages").appendChild(div);
  const container = $("chatMessages");
  container.scrollTop = container.scrollHeight;
  return div;
}

async function sendChat() {
  const text = $("chatInput").value.trim();
  if (!text) return;

  $("chatSendBtn").disabled = true;
  $("chatInput").disabled = true;

  appendChatMsg("user", text);
  _chatHistory.push({ role: "user", content: text });
  $("chatInput").value = "";

  const bubble = appendChatMsg("assistant", "", true);

  try {
    const resp = await fetch(`/api/${state.jobId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: _chatHistory }),
    });

    if (!resp.ok) {
      bubble.textContent = "Ошибка: " + resp.status;
      bubble.classList.remove("streaming");
    } else {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let full = "";
      let buf = "";
      let streamError = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") { buf = ""; break; }
          if (payload.startsWith("[ERROR]")) {
            bubble.textContent = "Ошибка агента: " + payload.slice(7).trim();
            streamError = true;
            break;
          }
          full += payload;
          bubble.textContent = full;
          $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
        }
      }

      bubble.classList.remove("streaming");
      if (!streamError && full) {
        _chatHistory.push({ role: "assistant", content: full });
      } else if (!streamError && !full) {
        bubble.textContent = "Агент не вернул ответ. Попробуйте ещё раз.";
        bubble.classList.add("chat-unavailable");
      }
    }
  } catch (e) {
    bubble.textContent = "Ошибка связи с агентом";
    bubble.classList.remove("streaming");
  } finally {
    $("chatInput").disabled = false;
    $("chatSendBtn").disabled = false;
    $("chatInput").focus();
  }
}

$("chatSendBtn").addEventListener("click", sendChat);
$("chatInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

// ── Sensitivity analysis ───────────────────────────────────────────────────

function sensRenderResults(data) {
  const kpiNames = data.kpi_names || [];
  const base = data.base || {};
  const scenarios = data.scenarios || [];
  const table = $("sensResultTable");

  // Build header row
  let html = "<thead><tr><th class='col-label'>Сценарий</th>";
  kpiNames.forEach(k => { html += `<th>${escapeHtml(k)}</th>`; });
  html += "</tr></thead><tbody>";

  // Base row
  html += "<tr class='row-base'><td class='cell-label'>База</td>";
  kpiNames.forEach(k => {
    const v = (base.kpi_values || {})[k];
    html += `<td>${fmtNum(v, "")}</td>`;
  });
  html += "</tr>";

  // Scenario rows — find the varied input name for grouping
  let lastInput = null;
  scenarios.forEach(s => {
    const changedInput = (data.input_names || []).find(name => {
      return (base.inputs || {})[name] !== s.inputs[name];
    }) || "";
    if (changedInput && changedInput !== lastInput) {
      lastInput = changedInput;
      html += `<tr><td colspan="${kpiNames.length + 1}" class="cell-label" style="padding:4px 8px;background:#f8fafd;font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em">${escapeHtml(changedInput)}</td></tr>`;
    }
    html += `<tr><td class="cell-label">${escapeHtml(s.label || "")}</td>`;
    kpiNames.forEach(k => {
      const base_v = (base.kpi_values || {})[k];
      const v = (s.kpi_values || {})[k];
      let cls = "";
      if (base_v != null && v != null && isFinite(Number(v)) && isFinite(Number(base_v))) {
        cls = Number(v) > Number(base_v) ? "sens-cell-up" : Number(v) < Number(base_v) ? "sens-cell-dn" : "";
      }
      html += `<td class="${cls}">${fmtNum(v, "")}</td>`;
    });
    html += "</tr>";
  });

  html += "</tbody>";
  table.innerHTML = html;
  $("sensResults").style.display = "";
}
