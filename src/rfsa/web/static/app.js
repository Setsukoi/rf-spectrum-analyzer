const UNITS = { Hz: 1, kHz: 1e3, MHz: 1e6, GHz: 1e9 };
const FREQ_UNIT_NAMES = Object.keys(UNITS);

const $ = (id) => document.getElementById(id);

const els = {
  banner: $("banner"),
  identity: $("identity"),
  address: $("address"),
  connect: $("connect"),
  fake: $("fake"),
  disconnect: $("disconnect"),
  apply: $("apply"),
  scan: $("scan"),
  clearHistory: $("clear-history"),
  result: $("result"),
  screen: $("screen"),
  screenEmpty: $("screen-empty"),
  records: $("records"),
  busyMask: $("busy-mask"),
  popup: $("popup"),
  popupText: $("popup-text"),
  popupOk: $("popup-ok"),
  center: $("center"),
  centerUnit: $("center-unit"),
  span: $("span"),
  spanUnit: $("span-unit"),
  rbw: $("rbw"),
  rbwUnit: $("rbw-unit"),
  vbw: $("vbw"),
  vbwUnit: $("vbw-unit"),
  points: $("points"),
  sweepTime: $("sweep-time"),
  refLevel: $("ref-level"),
  atten: $("atten"),
  preamp: $("preamp"),
  detector: $("detector"),
};

let selectedId = null;
let busy = false;

function fillUnitSelect(select) {
  select.innerHTML = FREQ_UNIT_NAMES.map(
    (name) => `<option value="${name}">${name}</option>`
  ).join("");
}

fillUnitSelect(els.centerUnit);
fillUnitSelect(els.spanUnit);
fillUnitSelect(els.rbwUnit);
fillUnitSelect(els.vbwUnit);
els.centerUnit.value = "MHz";
els.spanUnit.value = "MHz";
els.rbwUnit.value = "kHz";
els.vbwUnit.value = "kHz";

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  return detail ? JSON.stringify(detail) : "请求失败";
}

function showBanner(text, ok = false) {
  if (!text) {
    els.banner.hidden = true;
    return;
  }
  els.banner.hidden = false;
  els.banner.textContent = text;
  els.banner.classList.toggle("ok", ok);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailText(data.detail) || response.statusText);
  return data;
}

function pickUnit(hz) {
  const abs = Math.abs(Number(hz));
  if (abs >= 1e9) return "GHz";
  if (abs >= 1e6) return "MHz";
  if (abs >= 1e3) return "kHz";
  return "Hz";
}

function fromHz(hz, unit) {
  return Number(hz) / UNITS[unit];
}

function toHz(value, unit) {
  return Number(value) * UNITS[unit];
}

function fmtHz(hz) {
  if (hz == null || Number.isNaN(Number(hz))) return "—";
  const unit = pickUnit(hz);
  const value = fromHz(hz, unit);
  return `${Number(value.toPrecision(7))} ${unit}`;
}

function fmtTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function setFreq(input, unitSelect, hz) {
  const unit = pickUnit(hz);
  unitSelect.value = unit;
  const value = fromHz(hz, unit);
  input.value = Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(10)));
}

function fillForm(settings) {
  if (!settings) return;
  setFreq(els.center, els.centerUnit, settings.center_hz);
  setFreq(els.span, els.spanUnit, settings.span_hz);
  setFreq(els.rbw, els.rbwUnit, settings.rbw_hz);
  setFreq(els.vbw, els.vbwUnit, settings.vbw_hz);
  els.points.value = settings.points;
  els.sweepTime.value = "";
  els.refLevel.value = settings.ref_level_dbm;
  els.atten.value = settings.attenuation_db;
  els.preamp.checked = Boolean(settings.preamp);
  if (settings.detector) els.detector.value = settings.detector;
}

function numberOrNull(input) {
  const text = input.value.trim();
  if (text === "") return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function formPayload() {
  const payload = {
    center_hz: toHz(els.center.value, els.centerUnit.value),
    span_hz: toHz(els.span.value, els.spanUnit.value),
    rbw_hz: toHz(els.rbw.value, els.rbwUnit.value),
    points: Number(els.points.value),
    attenuation_db: Number(els.atten.value),
    preamp: els.preamp.checked,
    detector: els.detector.value,
  };
  const vbw = numberOrNull(els.vbw);
  const sweepTime = numberOrNull(els.sweepTime);
  const refLevel = numberOrNull(els.refLevel);
  if (vbw != null) payload.vbw_hz = toHz(vbw, els.vbwUnit.value);
  if (sweepTime != null) payload.sweep_time_s = sweepTime;
  if (refLevel != null) payload.ref_level_dbm = refLevel;
  return payload;
}

function setBusy(on, scanLabel) {
  busy = on;
  for (const button of [els.connect, els.fake, els.disconnect, els.apply, els.scan, els.clearHistory]) {
    button.disabled = on;
  }
  els.scan.textContent = on && scanLabel ? scanLabel : "扫描";
  els.busyMask.hidden = !(on && scanLabel);
}

function showResult(row) {
  if (!row) {
    els.result.textContent = "尚未扫描";
    showScreenshot(null);
    return;
  }
  const error = row.frequency_error_hz;
  const errorText = error == null ? "—" : `${error >= 0 ? "+" : ""}${Number(error).toFixed(3)} Hz`;
  els.result.innerHTML = [
    `<strong>${fmtTime(row.captured_at)}</strong>　${row.label || ""}`,
    `中心 ${fmtHz(row.center_hz)}　Span ${fmtHz(row.span_hz)}　RBW ${fmtHz(row.rbw_hz)}`,
    `峰值 ${Number(row.peak_dbm).toFixed(2)} dBm @ ${fmtHz(row.peak_hz)}`,
    `频率计 ${fmtHz(row.counter_hz)}　相对中心误差 ${errorText}`,
    row.has_screenshot
      ? `截图 ${row.screenshot_name}（screenshots/）`
      : "没有截图",
  ].join("<br>");
  showScreenshot(row);
}

function showScreenshot(row) {
  if (!row || !row.has_screenshot) {
    els.screen.hidden = true;
    els.screen.removeAttribute("src");
    els.screenEmpty.hidden = false;
    els.screenEmpty.textContent = row ? "无截图" : "扫描后这里显示仪器截图";
    return;
  }
  els.screenEmpty.hidden = true;
  els.screen.hidden = false;
  els.screen.alt = row.screenshot_name || "仪器截图";
  els.screen.src = `/api/sweeps/${row.id}/screenshot?t=${encodeURIComponent(row.captured_at || "")}`;
}

function showPopup(text) {
  els.popupText.textContent = text;
  els.popup.hidden = false;
}

function renderRecords(rows) {
  if (!rows.length) {
    els.records.innerHTML = '<p class="empty">还没有扫描记录</p>';
    return;
  }
  els.records.innerHTML = rows.map((row) => {
    const selected = row.id === selectedId ? " selected" : "";
    const error = row.frequency_error_hz;
    const errorText = error == null ? "" : `　误差 ${error >= 0 ? "+" : ""}${Number(error).toFixed(3)} Hz`;
    const shot = row.has_screenshot ? `截图 ${row.screenshot_name}` : "无截图";
    return `<button type="button" class="record${selected}" data-id="${row.id}">
      <div class="time">#${row.id}　${fmtTime(row.captured_at)}</div>
      <p>${row.label || ""}　中心 ${fmtHz(row.center_hz)}　Span ${fmtHz(row.span_hz)}　RBW ${fmtHz(row.rbw_hz)}</p>
      <p>峰值 ${Number(row.peak_dbm).toFixed(2)} dBm @ ${fmtHz(row.peak_hz)}</p>
      <p>频率计 ${fmtHz(row.counter_hz)}${errorText}</p>
      <p>${shot}</p>
    </button>`;
  }).join("");
}

async function refreshRecords() {
  const rows = await api("/api/sweeps");
  renderRecords(rows);
}

function applyStatus(status) {
  if (status.connected) {
    const kind = status.fake ? "测试数据" : "已连接";
    els.identity.textContent = `${kind}　${status.identity ? status.identity.text : ""}`;
    fillForm(status.settings);
  } else {
    els.identity.textContent = "未连接";
  }
}

async function loadSweep(id) {
  const row = await api(`/api/sweeps/${id}`);
  selectedId = id;
  showResult(row);
  await refreshRecords();
}

els.records.addEventListener("click", async (event) => {
  const button = event.target.closest(".record");
  if (!button || busy) return;
  try {
    await loadSweep(Number(button.dataset.id));
  } catch (err) {
    showBanner(err.message);
  }
});

async function withBusy(work, scanLabel) {
  if (busy) return;
  setBusy(true, scanLabel);
  showBanner("");
  try {
    await work();
  } catch (err) {
    showBanner(err.message);
  } finally {
    setBusy(false);
  }
}

els.connect.addEventListener("click", () => withBusy(async () => {
  const status = await api("/api/connect", {
    method: "POST",
    body: JSON.stringify({ address: els.address.value, fake: false }),
  });
  applyStatus(status);
  showBanner("已连接", true);
}));

els.fake.addEventListener("click", () => withBusy(async () => {
  const status = await api("/api/connect", {
    method: "POST",
    body: JSON.stringify({ fake: true }),
  });
  applyStatus(status);
  showBanner("已连接测试数据", true);
}));

els.disconnect.addEventListener("click", () => withBusy(async () => {
  applyStatus(await api("/api/disconnect", { method: "POST", body: "{}" }));
  showBanner("已断开", true);
}));

els.apply.addEventListener("click", () => withBusy(async () => {
  const settings = await api("/api/configure", {
    method: "POST",
    body: JSON.stringify(formPayload()),
  });
  fillForm(settings);
  showBanner(
    `已写入。RBW 实际 ${fmtHz(settings.rbw_hz)}，扫描时间 ${Number(settings.sweep_time_s).toFixed(4)} s`,
    true
  );
}));

els.popupOk.addEventListener("click", () => {
  els.popup.hidden = true;
});

els.clearHistory.addEventListener("click", () => {
  if (!window.confirm("清空全部扫描记录和截图？此操作不能恢复。")) return;
  withBusy(async () => {
    await api("/api/history/clear", { method: "POST", body: "{}" });
    selectedId = null;
    showResult(null);
    await refreshRecords();
    showBanner("历史已清空", true);
  });
});

els.scan.addEventListener("click", () => withBusy(async () => {
  const row = await api("/api/scan", {
    method: "POST",
    body: JSON.stringify(formPayload()),
  });
  selectedId = row.id;
  fillForm(row.settings);
  showResult(row);
  await refreshRecords();
  if (row.applied) {
    showPopup("已自动应用当前参数");
  }
  if (!row.has_screenshot) {
    showBanner(`已保存扫描 #${row.id}（无截图）`);
  } else {
    showBanner(`已保存扫描 #${row.id}`, true);
  }
}, "扫描中…"));

(async function init() {
  try {
    applyStatus(await api("/api/status"));
    await refreshRecords();
    showScreenshot(null);
  } catch (err) {
    showBanner(err.message);
  }
})();
