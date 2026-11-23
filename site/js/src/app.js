const AAD = new TextEncoder().encode("sanghanga-v1");
const PASS_KEY = "sanghanga.pass";
let cryptoKey = null;
let cryptoHeader = null;

const SIZE_LABEL = { 대: "size-lg", 중: "size-md", 소: "size-sm" };
const MARKET = { KOSPI: "코스피", KOSDAQ: "코스닥" };
const DOW = ["일", "월", "화", "수", "목", "금", "토"];

const state = {
  manifest: null,
  months: {},
  view: "today",
  month: "2026-01",
  size: "all",
  query: "",
  selectedDate: "",
  chart: null,
};

const app = document.querySelector("#app");
const sheet = document.querySelector("#sheet");
const modal = document.querySelector("#modal");

function kstToday() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function koreanDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${y}년 ${m}월 ${d}일`;
}

function monthLabel(id) {
  const [y, m] = id.split("-").map(Number);
  return `${y}년 ${m}월`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function price(value) {
  if (value == null || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("ko-KR");
}

function rate(value) {
  const number = Number(value);
  const text = `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
  return text;
}

async function loadCryptoHeader() {
  const response = await fetch("data/crypto.json", { cache: "no-cache" });
  if (!response.ok) {
    const error = new Error("missing-header");
    error.code = "missing-header";
    throw error;
  }
  cryptoHeader = await response.json();
}

async function deriveKey(password) {
  const salt = Uint8Array.from(atob(cryptoHeader.salt), (char) => char.charCodeAt(0));
  const base = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  cryptoKey = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: cryptoHeader.iterations, hash: "SHA-256" },
    base,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"]
  );
}

async function decryptJson(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (response.status === 404) {
    const error = new Error("missing");
    error.code = "missing";
    throw error;
  }
  if (!response.ok) throw new Error("http");
  const blob = new Uint8Array(await response.arrayBuffer());
  const plain = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: blob.slice(0, 12), additionalData: AAD, tagLength: 128 },
    cryptoKey,
    blob.slice(12)
  );
  return JSON.parse(new TextDecoder().decode(plain));
}

function badPassword(error) {
  return error && (error.name === "OperationError" || error.name === "InvalidAccessError");
}

async function loadManifest() {
  state.manifest = await decryptJson("data/enc/manifest.bin");
  const months = state.manifest.months || [];
  state.month = months.length ? months[months.length - 1].id : "2026-01";
  const updated = document.querySelector("#updated");
  const latest = state.manifest.latestTradingDay;
  updated.textContent = latest
    ? `최근 거래일 ${koreanDate(latest)} · 네이버금융`
    : "수집된 시세가 없습니다";
}

async function loadMonth(id) {
  if (!state.months[id]) {
    const stamp = encodeURIComponent(state.manifest.updatedAt || "");
    state.months[id] = await decryptJson(`data/enc/months/${id}.bin?v=${stamp}`);
  }
  return state.months[id];
}

function matches(text) {
  const query = state.query.trim();
  if (!query) return true;
  return text.toLowerCase().includes(query.toLowerCase());
}

function stockButton(row, extra) {
  const reasonClass = row.reasonFound ? "reason" : "reason missing";
  const source = row.sourceUrl
    ? `<a href="${escapeHtml(row.sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.source || "기사")}</a>`
    : `<span>${escapeHtml(row.source || "")}</span>`;
  const tag = row.aboveWeekly240 ? `<span class="tag on">주봉240</span>` : "";
  const inferred = row.inferred ? `<span class="tag infer">추정</span>` : "";
  return `<li><button type="button" class="stock" data-code="${escapeHtml(row.code)}">
    <div class="row"><b>${escapeHtml(row.name)}</b><span class="mkt">${MARKET[row.market] || ""}</span>${tag}${inferred}<span class="rate">${rate(row.changeRate)}</span></div>
    <p class="${reasonClass}">${escapeHtml(row.reason || "이유 미확인")}</p>
    <div class="meta"><span>${escapeHtml(extra || row.code)}</span>${source}</div>
  </button></li>`;
}

function themeHead(theme, open) {
  const inferred = theme.inferredStocks ? `<span class="tag infer">추정 ${theme.inferredStocks}</span>` : "";
  return `<button type="button" class="head" data-toggle="1" aria-expanded="${open ? "true" : "false"}">
      <span class="num">${escapeHtml(theme.id)}</span>
      <h2>${escapeHtml(theme.name)}</h2>
      ${inferred}
      <span class="size ${SIZE_LABEL[theme.size] || "size-sm"}">${escapeHtml(theme.size)}</span>
      <span class="count">${theme.stockCount}종목</span>
    </button>`;
}

function themeBlock(theme, rows, open) {
  const list = rows.map((row) => stockButton(row, `${row.code}`)).join("");
  return `<section class="theme">
    ${themeHead(theme, open)}
    <ul class="stocks" ${open ? "" : "hidden"}>${list}</ul>
  </section>`;
}

function toolbar(placeholder) {
  return `<div class="tools">
    <input id="q" type="search" placeholder="${placeholder}" value="${escapeHtml(state.query)}" enterkeyhint="search">
    <div class="chips" role="group" aria-label="테마 크기">
      ${["all", "대", "중", "소"].map((size) => `<button type="button" data-size="${size}" class="${state.size === size ? "on" : ""}">${size === "all" ? "전체" : size}</button>`).join("")}
    </div>
  </div>
  <details class="note"><summary>대 · 중 · 소 기준</summary>
    이번 달 그 테마에 들어간 상한가 종목 수입니다. 대는 3개 이상이고 그중 하나 이상이 주봉 240선 위이거나 그 선을 돌파한 경우, 중은 2개이거나 3개 이상이지만 주봉 240선 위/돌파가 없는 경우, 소는 1개입니다. 비슷한 이유와 같은 업종은 한 테마로 모이고, 다음 달에는 번호가 다시 시작됩니다. 뉴스가 없는 종목은 이유 문장을 만들지 않습니다. 테마만 추정한 경우 추정이라고 표시하며, 그 종목도 테마 수에 포함됩니다.
  </details>`;
}

async function renderToday() {
  const today = kstToday();
  const latest = state.manifest.latestTradingDay;
  if (!latest) {
    app.innerHTML = `<p class="empty">아직 수집된 상한가가 없습니다.</p>`;
    return;
  }
  let show = latest;
  let note = "";
  try {
    const todayMonth = await loadMonth(today.slice(0, 7));
    if (todayMonth.tradingDays.includes(today)) {
      show = today;
    } else if (today.slice(0, 7) === todayMonth.month) {
      note = `${koreanDate(today)}은 휴장일입니다. 최근 거래일 ${koreanDate(latest)} 상한가를 보여 줍니다.`;
    }
  } catch (_error) {
    if (today !== latest) {
      note = `${koreanDate(today)} 시세는 아직 없습니다. 최근 거래일 ${koreanDate(latest)} 상한가입니다.`;
    }
  }
  const month = await loadMonth(show.slice(0, 7));
  const rows = (month.byDate && month.byDate[show]) || [];
  const filtered = rows.filter((row) => (state.size === "all" || row.themeSize === state.size) && matches(`${row.name} ${row.code} ${row.reason} ${row.themeName}`));
  const groups = new Map();
  for (const row of filtered) {
    if (!groups.has(row.themeId)) groups.set(row.themeId, []);
    groups.get(row.themeId).push(row);
  }
  const themes = (month.themes || []).filter((theme) => groups.has(theme.id));
  const body = themes.length
    ? themes.map((theme) => themeBlock(theme, groups.get(theme.id), true)).join("")
    : `<p class="empty">${koreanDate(show)} 상한가 종목이 없습니다.</p>`;
  const title = show === today ? `오늘 · ${koreanDate(show)}` : koreanDate(show);
  app.innerHTML = `${note ? `<p class="banner">${escapeHtml(note)}</p>` : ""}
    <p class="note">${escapeHtml(title)} · ${rows.length}건</p>
    ${toolbar("종목, 이유, 테마 검색")}
    ${body}`;
}

function shiftMonth(id, delta) {
  const [year, month] = id.split("-").map(Number);
  const date = new Date(year, month - 1 + delta, 1);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

async function renderCalendar() {
  const ids = (state.manifest.months || []).map((month) => month.id);
  const min = ids[0] || "2026-01";
  const max = ids[ids.length - 1] || min;
  if (state.month < min) state.month = min;
  if (state.month > max) state.month = max;
  const month = await loadMonth(state.month);
  const [year, monthNumber] = state.month.split("-").map(Number);
  const first = new Date(year, monthNumber - 1, 1);
  const days = new Date(year, monthNumber, 0).getDate();
  const trading = new Set(month.tradingDays || []);
  const byDate = month.byDate || {};
  let cells = DOW.map((name) => `<div class="dow">${name}</div>`).join("");
  for (let i = 0; i < first.getDay(); i += 1) cells += `<button type="button" disabled></button>`;
  for (let day = 1; day <= days; day += 1) {
    const iso = `${state.month}-${String(day).padStart(2, "0")}`;
    const count = (byDate[iso] || []).length;
    const weekend = new Date(year, monthNumber - 1, day).getDay() % 6 === 0;
    const open = trading.has(iso);
    const disabled = !open && !count;
    const title = weekend ? "주말" : open ? (count ? `상한가 ${count}건` : "상한가 없음") : "휴장";
    cells += `<button type="button" data-date="${iso}" class="${count ? "has" : ""} ${state.selectedDate === iso ? "on" : ""}" ${disabled ? "disabled" : ""} title="${title}">${day}${count ? `<span class="n">${count}</span>` : ""}</button>`;
  }
  const themes = (month.themes || []).filter((theme) => {
    if (state.size !== "all" && theme.size !== state.size) return false;
    const blob = `${theme.name} ${theme.id} ` + theme.stocks.map((stock) => `${stock.name} ${stock.code} ${stock.events.map((event) => event.reason).join(" ")}`).join(" ");
    return matches(blob);
  });
  const themeHtml = themes.map((theme) => {
    const rows = [];
    theme.stocks.forEach((stock) => {
      stock.events.forEach((event) => {
        rows.push({ ...event, code: stock.code, name: stock.name, market: stock.market, themeSize: theme.size });
      });
    });
    const themeHit = matches(`${theme.id} ${theme.name}`);
    const visible = themeHit ? rows : rows.filter((row) => matches(`${row.name} ${row.code} ${row.reason}`));
    const list = visible.map((row) => stockButton(row, row.date)).join("");
    return `<section class="theme">
      ${themeHead(theme, false)}
      <ul class="stocks" hidden>${list}</ul>
    </section>`;
  }).join("");
  const summary = state.manifest.months.find((item) => item.id === state.month);
  app.innerHTML = `<div class="month-nav">
      <button type="button" id="prev" ${state.month <= min ? "disabled" : ""} aria-label="이전 달">이전</button>
      <h2>${monthLabel(state.month)}</h2>
      <button type="button" id="next" ${state.month >= max ? "disabled" : ""} aria-label="다음 달">다음</button>
    </div>
    <p class="note">상한가 ${summary ? summary.events : month.eventCount}건 · 테마 ${summary ? summary.themes : month.themeCount}개. 날짜를 누르면 그날의 상한가가 열립니다.</p>
    <div class="cal">${cells}</div>
    ${toolbar("테마나 종목 검색")}
    ${themeHtml || `<p class="empty">이 달에 표시할 테마가 없습니다.</p>`}`;
}

function openSheet(date, rows) {
  const list = rows.length
    ? `<ul class="stocks">${rows.map((row) => stockButton(row, row.themeId + " " + row.themeName)).join("")}</ul>`
    : `<p class="empty">이 날 상한가 종목이 없습니다.</p>`;
  sheet.hidden = false;
  sheet.innerHTML = `<div class="panel" role="dialog" aria-modal="true" aria-label="${koreanDate(date)}">
    <header class="sheet-head"><div class="grow"><h3>${koreanDate(date)}</h3><p class="muted">${rows.length}건</p></div>
      <button type="button" class="icon" data-close="sheet" aria-label="닫기">×</button></header>
    <div class="body">${list}</div>
  </div>`;
  document.body.classList.add("lock");
}

function closeSheet() {
  sheet.hidden = true;
  sheet.innerHTML = "";
  if (modal.hidden) document.body.classList.remove("lock");
}

async function openChart(code, name) {
  modal.hidden = false;
  document.body.classList.add("lock");
  modal.innerHTML = `<div class="panel" role="dialog" aria-modal="true" aria-label="${escapeHtml(name || code)} 차트">
    <header class="modal-head"><div class="grow"><h3 id="chart-title">${escapeHtml(name || code)}</h3><p class="muted" id="chart-sub">${escapeHtml(code)}</p></div>
      <button type="button" class="icon" data-close="modal" aria-label="닫기">×</button></header>
    <div class="segment" role="tablist">
      <button type="button" data-mode="daily" class="on">일봉</button>
      <button type="button" data-mode="weekly">주봉</button>
      <button type="button" data-mode="monthly">월봉</button>
    </div>
    <div class="ohlc" id="ohlc"></div>
    <canvas class="chart" id="chart"></canvas>
    <p class="hint" id="chart-hint">끌어서 이전 시세를 볼 수 있습니다.</p>
  </div>`;
  try {
    const stamp = encodeURIComponent(state.manifest.updatedAt || "");
    const payload = await decryptJson(`data/enc/charts/${encodeURIComponent(code)}.bin?v=${stamp}`);
    document.querySelector("#chart-title").textContent = payload.name || name || code;
    document.querySelector("#chart-sub").textContent = `${payload.code} · ${MARKET[payload.market] || payload.market || ""}`;
    state.chart = { payload, mode: "daily", end: null };
    drawActiveChart();
  } catch (_error) {
    document.querySelector("#chart-hint").textContent = "차트 데이터를 찾지 못했습니다.";
  }
}

function closeModal() {
  state.chart = null;
  modal.hidden = true;
  modal.innerHTML = "";
  if (sheet.hidden) document.body.classList.remove("lock");
}

function seriesOf(mode) {
  const payload = state.chart.payload;
  if (mode === "weekly") return payload.weekly || [];
  if (mode === "monthly") return payload.monthly || [];
  return payload.daily || [];
}

function visibleCount(mode) {
  if (mode === "monthly") return 48;
  if (mode === "weekly") return 70;
  return 90;
}

function drawActiveChart() {
  const canvas = document.querySelector("#chart");
  if (!canvas || !state.chart) return;
  const series = seriesOf(state.chart.mode);
  const hint = document.querySelector("#chart-hint");
  if (!series.length) {
    hint.textContent = "표시할 시세가 없습니다.";
    return;
  }
  const count = visibleCount(state.chart.mode);
  if (state.chart.end == null || state.chart.end > series.length) state.chart.end = series.length;
  if (state.chart.end < Math.min(count, series.length)) state.chart.end = Math.min(count, series.length);
  const end = state.chart.end;
  const start = Math.max(0, end - count);
  const view = series.slice(start, end);
  if (state.chart.mode === "weekly") hint.textContent = "주봉 노란 선은 240주 이동평균입니다. 데이터가 240주보다 짧으면 선이 없습니다.";
  else if (state.chart.mode === "monthly") hint.textContent = "월봉 노란 선은 120개월 이동평균입니다. 데이터가 120개월보다 짧으면 선이 없습니다.";
  else hint.textContent = "일봉입니다. 좌우로 끌면 이전 캔들을 볼 수 있습니다.";
  paint(canvas, view, state.chart.hover);
}

function paint(canvas, view, hover) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 320;
  const height = canvas.clientHeight || 360;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = { l: 8, r: 62, t: 14, b: 24, vol: 64 };
  const plotH = height - pad.t - pad.b - pad.vol;
  const plotW = width - pad.l - pad.r;
  let low = Infinity;
  let high = -Infinity;
  let maxVol = 0;
  view.forEach((bar) => {
    low = Math.min(low, bar.l, bar.ma ?? bar.l);
    high = Math.max(high, bar.h, bar.ma ?? bar.h);
    maxVol = Math.max(maxVol, bar.v || 0);
  });
  if (!Number.isFinite(low) || low === high) {
    low = (view[0]?.c || 0) * 0.98;
    high = (view[0]?.c || 1) * 1.02;
  }
  const span = high - low || 1;
  const slot = plotW / view.length;
  const x = (index) => pad.l + slot * index + slot / 2;
  const y = (value) => pad.t + (high - value) / span * plotH;
  ctx.strokeStyle = "#2a3140";
  ctx.lineWidth = 1;
  ctx.font = "11px sans-serif";
  ctx.fillStyle = "#9aa3b2";
  for (let step = 0; step <= 4; step += 1) {
    const value = high - span * step / 4;
    const py = y(value);
    ctx.beginPath();
    ctx.moveTo(pad.l, py);
    ctx.lineTo(width - pad.r, py);
    ctx.stroke();
    ctx.fillText(price(Math.round(value)), width - pad.r + 6, py + 4);
  }
  view.forEach((bar, index) => {
    const up = bar.c >= bar.o;
    ctx.strokeStyle = up ? "#ff5a64" : "#5b93ff";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.beginPath();
    ctx.moveTo(x(index), y(bar.h));
    ctx.lineTo(x(index), y(bar.l));
    ctx.stroke();
    const top = y(Math.max(bar.o, bar.c));
    const bottom = y(Math.min(bar.o, bar.c));
    const body = Math.max(1, bottom - top);
    const bw = Math.max(1, Math.min(8, slot * 0.62));
    ctx.fillRect(x(index) - bw / 2, top, bw, body);
    const vh = maxVol ? ((bar.v || 0) / maxVol) * (pad.vol - 16) : 0;
    ctx.globalAlpha = 0.45;
    ctx.fillRect(x(index) - bw / 2, height - pad.b - vh, bw, vh);
    ctx.globalAlpha = 1;
  });
  ctx.beginPath();
  let started = false;
  view.forEach((bar, index) => {
    if (bar.ma == null) return;
    const px = x(index);
    const py = y(bar.ma);
    if (!started) {
      ctx.moveTo(px, py);
      started = true;
    } else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = "#e2b340";
  ctx.lineWidth = 1.6;
  ctx.stroke();
  const labelEvery = Math.ceil(view.length / 4);
  ctx.fillStyle = "#9aa3b2";
  ctx.lineWidth = 1;
  view.forEach((bar, index) => {
    if (index % labelEvery !== 0 && index !== view.length - 1) return;
    ctx.fillText(bar.d.slice(2), x(index) - 18, height - 6);
  });
  if (hover != null && view[hover]) {
    const bar = view[hover];
    ctx.strokeStyle = "#d7dde6";
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x(hover), pad.t);
    ctx.lineTo(x(hover), height - pad.b);
    ctx.stroke();
    ctx.setLineDash([]);
    const ohlc = document.querySelector("#ohlc");
    if (ohlc) {
      const ma = bar.ma != null ? ` · 이평 ${price(bar.ma)}` : "";
      ohlc.textContent = `${bar.d}  시 ${price(bar.o)}  고 ${price(bar.h)}  저 ${price(bar.l)}  종 ${price(bar.c)}${ma}`;
    }
  }
}

function bindChartPointer() {
  const canvas = document.querySelector("#chart");
  if (!canvas || canvas.dataset.bound) return;
  canvas.dataset.bound = "1";
  let drag = null;
  const indexAt = (clientX) => {
    const rect = canvas.getBoundingClientRect();
    const series = seriesOf(state.chart.mode);
    const count = visibleCount(state.chart.mode);
    const end = state.chart.end;
    const start = Math.max(0, end - count);
    const viewLen = end - start;
    const padL = 8;
    const padR = 62;
    const slot = (rect.width - padL - padR) / viewLen;
    return Math.max(0, Math.min(viewLen - 1, Math.floor((clientX - rect.left - padL) / slot)));
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (!state.chart) return;
    drag = { x: event.clientX, end: state.chart.end };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!state.chart) return;
    if (drag) {
      const slot = (canvas.clientWidth - 70) / visibleCount(state.chart.mode);
      const shift = Math.round((drag.x - event.clientX) / Math.max(slot, 1));
      const series = seriesOf(state.chart.mode);
      const count = visibleCount(state.chart.mode);
      const next = Math.max(Math.min(count, series.length), Math.min(series.length, drag.end + shift));
      state.chart.end = next;
      state.chart.hover = null;
      drawActiveChart();
      return;
    }
    state.chart.hover = indexAt(event.clientX);
    drawActiveChart();
  });
  const stop = () => { drag = null; };
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);
}

async function render() {
  if (state.view === "calendar") await renderCalendar();
  else await renderToday();
  const params = new URLSearchParams(location.search);
  params.set("view", state.view);
  if (state.view === "calendar") params.set("month", state.month);
  history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
}

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", async () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".tabs button").forEach((item) => item.classList.toggle("on", item === button));
    await render();
  });
});

app.addEventListener("click", async (event) => {
  const size = event.target.closest("[data-size]");
  if (size) {
    state.size = size.dataset.size;
    await render();
    return;
  }
  const toggle = event.target.closest("[data-toggle]");
  if (toggle) {
    const list = toggle.parentElement.querySelector(".stocks");
    const open = list.hasAttribute("hidden");
    list.toggleAttribute("hidden", !open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    return;
  }
  if (event.target.id === "prev" || event.target.id === "next") {
    state.month = shiftMonth(state.month, event.target.id === "prev" ? -1 : 1);
    state.selectedDate = "";
    await render();
    return;
  }
  const dateButton = event.target.closest("[data-date]");
  if (dateButton && !dateButton.disabled) {
    state.selectedDate = dateButton.dataset.date;
    const month = await loadMonth(state.selectedDate.slice(0, 7));
    openSheet(state.selectedDate, (month.byDate && month.byDate[state.selectedDate]) || []);
    await render();
    return;
  }
  const stock = event.target.closest(".stock");
  if (stock && !event.target.closest("a")) {
    const code = stock.dataset.code;
    const name = stock.querySelector("b")?.textContent || code;
    await openChart(code, name);
    bindChartPointer();
  }
});

app.addEventListener("input", (event) => {
  if (event.target.id !== "q") return;
  state.query = event.target.value;
  const caret = event.target.selectionStart;
  render().then(() => {
    const field = document.querySelector("#q");
    if (!field) return;
    field.focus();
    field.setSelectionRange(caret, caret);
  });
});

document.body.addEventListener("click", async (event) => {
  const mode = event.target.closest("[data-mode]");
  if (mode && state.chart) {
    state.chart.mode = mode.dataset.mode;
    state.chart.end = null;
    state.chart.hover = null;
    document.querySelectorAll("[data-mode]").forEach((button) => button.classList.toggle("on", button === mode));
    drawActiveChart();
    return;
  }
  if (event.target.dataset.close === "sheet" || event.target === sheet) closeSheet();
  if (event.target.dataset.close === "modal" || event.target === modal) closeModal();
  const stock = event.target.closest("#sheet .stock");
  if (stock && !event.target.closest("a")) {
    await openChart(stock.dataset.code, stock.querySelector("b")?.textContent || stock.dataset.code);
    bindChartPointer();
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!modal.hidden) closeModal();
    else closeSheet();
  }
});

window.addEventListener("resize", () => {
  if (state.chart) drawActiveChart();
});

async function boot() {
  try {
    const params = new URLSearchParams(location.search);
    if (params.get("view") === "calendar") {
      state.view = "calendar";
      document.querySelector("#tab-today").classList.remove("on");
      document.querySelector("#tab-calendar").classList.add("on");
    }
    if (params.get("month")) state.month = params.get("month");
    await render();
    if (params.get("date")) {
      state.selectedDate = params.get("date");
      const month = await loadMonth(state.selectedDate.slice(0, 7));
      openSheet(state.selectedDate, (month.byDate && month.byDate[state.selectedDate]) || []);
    }
    if (params.get("stock")) {
      await openChart(params.get("stock"), params.get("stock"));
      bindChartPointer();
    }
  } catch (error) {
    app.innerHTML = `<p class="empty">데이터를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.</p>`;
    console.error(error);
  }
}

async function unlock(password) {
  await deriveKey(password);
  await loadManifest();
  document.querySelector("#gate").hidden = true;
  document.querySelector("#shell").hidden = false;
  await boot();
}

function showGate(text) {
  const message = document.querySelector("#gate-msg");
  message.hidden = false;
  message.textContent = text;
}

async function startGate() {
  const form = document.querySelector("#unlock");
  const input = document.querySelector("#password");
  const remember = document.querySelector("#remember");
  try {
    await loadCryptoHeader();
  } catch (_error) {
    showGate("아직 수집된 데이터가 없습니다. 암호를 설정한 뒤 수집이 끝나면 열 수 있습니다.");
    form.querySelector("button").disabled = true;
    input.disabled = true;
    return;
  }
  const saved = localStorage.getItem(PASS_KEY);
  if (saved) {
    input.value = saved;
    remember.checked = true;
    try {
      await unlock(saved);
      return;
    } catch (error) {
      localStorage.removeItem(PASS_KEY);
      input.value = "";
      showGate(badPassword(error) ? "저장된 암호가 맞지 않습니다." : "저장된 암호로 열지 못했습니다.");
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    document.querySelector("#gate-msg").hidden = true;
    const password = input.value;
    try {
      await unlock(password);
      if (remember.checked) localStorage.setItem(PASS_KEY, password);
      else localStorage.removeItem(PASS_KEY);
    } catch (error) {
      if (error && error.code === "missing") {
        showGate("아직 수집된 데이터가 없습니다. 수집이 끝나면 다시 열어 주세요.");
        return;
      }
      showGate(badPassword(error) ? "암호가 맞지 않습니다." : "암호가 맞지 않거나 데이터를 열 수 없습니다.");
    }
  });
}

startGate();
