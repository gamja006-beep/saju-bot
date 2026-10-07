"use strict";

// 대한민국 주요 도시 대표 경도(동경). "모름"은 경도 없음 -> 미보정.
var CITIES = [
  { name: "선택 안 함 / 모름", lon: null },
  { name: "서울", lon: 126.978 },
  { name: "부산", lon: 129.075 },
  { name: "인천", lon: 126.705 },
  { name: "대구", lon: 128.601 },
  { name: "대전", lon: 127.385 },
  { name: "광주", lon: 126.853 },
  { name: "울산", lon: 129.311 },
  { name: "수원", lon: 127.029 },
  { name: "춘천", lon: 127.734 },
  { name: "강릉", lon: 128.896 },
  { name: "전주", lon: 127.148 },
  { name: "청주", lon: 127.489 },
  { name: "포항", lon: 129.365 },
  { name: "제주", lon: 126.531 },
  { name: "속초", lon: 128.592 }
];

// 상품: 실제 결제 미연동(준비 중). 'expert' 뱃지는 실제 전문가 검토 상품에만.
var PRODUCTS = [
  { id: "free", name: "무료 명식", price: "0원", kind: "free", desc: "출생정보로 사주 4주를 즉시 확인합니다.", delivery: "화면 즉시" },
  { id: "basic_9900", name: "기본 해석", price: "9,900원", kind: "ai", desc: "AI 기반 자동 해석 보고서.", delivery: "화면 자동 제공" },
  { id: "deep_39000", name: "심층 보고서", price: "39,000원", kind: "ai", desc: "AI 기반 자동 해석을 이메일로 발송.", delivery: "이메일 · 24시간 이내" },
  { id: "expert_99000", name: "전문가 보고서", price: "99,000원", kind: "expert", desc: "실제 전문가가 검토하는 보고서.", delivery: "이메일 · 1~3영업일" },
  { id: "life_290000", name: "인생설계 보고서", price: "290,000원", kind: "ai", desc: "AI 심층 분석 + 비대면 추가질문 1회.", delivery: "이메일" },
  { id: "relation_590000", name: "관계·사업 보고서", price: "590,000원", kind: "ai", desc: "복수 명식 분석 + 비대면 추가질문 2회.", delivery: "이메일" },
  { id: "vip_990000", name: "연간 VIP", price: "990,000원", kind: "ai", desc: "연간·분기별 이메일 보고서.", delivery: "이메일 · 연간" }
];

var TOTAL_STEPS = 8;
var current = 1;
var lastSaju = null;
var selectedProduct = "free";

var form = document.getElementById("intake");
var steps = Array.prototype.slice.call(document.querySelectorAll(".step"));
var prevBtn = document.getElementById("prev");
var nextBtn = document.getElementById("next");
var bar = document.getElementById("progress-bar");
var stepLabel = document.getElementById("step-label");
var formError = document.getElementById("form-error");

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function setError(msg) { formError.textContent = msg || ""; }
function val(name) { var el = form.elements[name]; return el ? el.value : ""; }
function checked(name) {
  var els = form.querySelectorAll('input[name="' + name + '"]:checked');
  return els.length ? els[0].value : "";
}

// 도시 목록 채우기
(function fillCities() {
  var sel = document.getElementById("city-select");
  CITIES.forEach(function (c, i) {
    var o = document.createElement("option");
    o.value = String(i);
    o.textContent = c.name;
    sel.appendChild(o);
  });
  sel.addEventListener("change", syncRegion);
})();

function syncCalendar() {
  var lunar = checked("calendar") === "lunar";
  document.getElementById("leap-row").hidden = !lunar;
  if (!lunar) form.elements["is_leap_month"].checked = false;
}
function syncTimeStatus() {
  var st = checked("time_status");
  document.getElementById("time-exact-row").hidden = st !== "exact";
  document.getElementById("time-approx-row").hidden = st !== "approx";
  document.getElementById("time-unknown-hint").hidden = st !== "unknown";
}
function syncRegion() {
  var idx = parseInt(document.getElementById("city-select").value, 10) || 0;
  document.getElementById("region-unknown-hint").hidden = CITIES[idx].lon !== null;
}
Array.prototype.forEach.call(form.querySelectorAll('input[name="calendar"]'), function (r) {
  r.addEventListener("change", syncCalendar);
});
Array.prototype.forEach.call(form.querySelectorAll('input[name="time_status"]'), function (r) {
  r.addEventListener("change", syncTimeStatus);
});

function midpoint(a, b) {
  function toMin(t) { var p = t.split(":"); return parseInt(p[0], 10) * 60 + parseInt(p[1], 10); }
  if (!a || !b) return a || b || "";
  var m = Math.round((toMin(a) + toMin(b)) / 2);
  var hh = String(Math.floor(m / 60)).padStart(2, "0");
  var mm = String(m % 60).padStart(2, "0");
  return hh + ":" + mm;
}

function birthTime() {
  var st = checked("time_status");
  if (st === "exact") return val("birth_time") || null;
  if (st === "approx") { var r = midpoint(val("birth_time_from"), val("birth_time_to")); return r || null; }
  return null; // unknown
}

function cityInfo() {
  var idx = parseInt(document.getElementById("city-select").value, 10) || 0;
  return CITIES[idx];
}

function topicsSelected() {
  return Array.prototype.map.call(
    form.querySelectorAll('input[name="topics"]:checked'), function (e) { return e.value; });
}

function basePayload() {
  var p = {
    calendar: checked("calendar"),
    birth_date: val("birth_date"),
    is_leap_month: !!form.elements["is_leap_month"].checked,
    gender: checked("gender"),
    birth_time: birthTime()
  };
  var city = cityInfo();
  var bp = { country: "KR", city: city.lon === null ? "" : city.name };
  if (city.lon !== null) bp.longitude = city.lon;
  p.birth_place = bp;
  return p;
}

function reportPayload() {
  var p = basePayload();
  p.alias = val("alias");
  p.consultation_type = checked("consultation_type");
  p.topics = topicsSelected();
  p.question = val("question");
  p.situation = val("situation");
  p.fortune_requested = !!form.elements["fortune_requested"].checked;
  p.target_period = val("target_period");
  p.selected_product = selectedProduct;
  return p;
}

function pillarCell(p) {
  if (!p) return "<td><span class=\"gz\">-</span></td>";
  return "<td><span class=\"gz\">" + esc(p.ganzhi) + "</span><span class=\"ko\">" +
    esc((p.gan_ko || "") + (p.zhi_ko || "")) + "</span></td>";
}

function renderMyeongsik(data) {
  var box = document.getElementById("myeongsik");
  var p = data.pillars;
  var html = "<table class=\"pillars\"><tr><th>연주</th><th>월주</th><th>일주</th><th>시주</th></tr><tr>" +
    pillarCell(p.year) + pillarCell(p.month) + pillarCell(p.day) + pillarCell(p.time) + "</tr></table>";
  html += "<div class=\"meta\">양력 " + esc(data.solar.year) + "-" +
    String(data.solar.month).padStart(2, "0") + "-" + String(data.solar.day).padStart(2, "0");
  if (data.solar.time) html += " " + esc(data.solar.time);
  html += "<br>" + esc(data.lunar.text) + "</div>";
  var tc = data.time_correction || {};
  html += "<div class=\"meta\">시간·지역 보정: " + (tc.applied ? "적용됨" : "미적용") + "</div>";
  if (!p.time) html += "<div class=\"warn\">출생 시간 미상 — 시주를 계산하지 않았습니다.</div>";
  if (data.needs_confirmation) html += "<div class=\"warn\">출생지(경도) 미입력 — 진태양시 보정이 적용되지 않았습니다.</div>";
  if (data.boundary_warning) html += "<div class=\"warn\">" + esc(data.boundary_warning.message) + "</div>";
  box.innerHTML = html;
}

function renderProducts() {
  var box = document.getElementById("products");
  box.innerHTML = "";
  PRODUCTS.forEach(function (pr) {
    var div = document.createElement("div");
    div.className = "product" + (pr.kind === "free" ? " free" : "");
    var badge = pr.kind === "expert"
      ? "<span class=\"badge expert\">전문가 검토</span>"
      : (pr.kind === "ai" ? "<span class=\"badge ai\">AI 기반 자동 해석</span>" : "");
    var html = "<div><span class=\"name\">" + esc(pr.name) + "</span><span class=\"price\">" + esc(pr.price) + "</span></div>";
    html += "<div class=\"desc\">" + badge + esc(pr.desc) + "</div>";
    html += "<div class=\"meta\">제공: " + esc(pr.delivery) + "</div>";
    if (pr.kind !== "free") {
      html += "<button type=\"button\" class=\"pay\" disabled>결제 기능 준비 중</button>";
    } else {
      html += "<div class=\"meta\">6단계에서 바로 확인할 수 있습니다.</div>";
    }
    div.innerHTML = html;
    div.addEventListener("click", function () {
      selectedProduct = pr.id;
      Array.prototype.forEach.call(box.children, function (c) { c.style.outline = ""; });
      div.style.outline = "2px solid #5b6ef5";
    });
    box.appendChild(div);
  });
}

function validateStep(n) {
  setError("");
  if (n === 2) {
    if (!val("birth_date")) { setError("생년월일을 입력해 주세요."); return false; }
  }
  if (n === 3) {
    var st = checked("time_status");
    if (st === "exact" && !val("birth_time")) { setError("정확한 시간을 입력하거나 '대략' 또는 '몰라요'를 선택해 주세요."); return false; }
  }
  return true;
}

function fetchJSON(url, payload) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); });
}

function onEnter(n) {
  if (n === 6) {
    var box = document.getElementById("myeongsik");
    box.textContent = "명식 계산 중...";
    fetchJSON("/saju", basePayload()).then(function (res) {
      if (res.ok && res.body.status === "ok") { lastSaju = res.body; renderMyeongsik(res.body); }
      else { box.textContent = res.body.message || "입력을 확인해 주세요."; }
    }).catch(function () { box.textContent = "오류가 발생했습니다."; });
  } else if (n === 7) {
    renderProducts();
  } else if (n === 8) {
    document.getElementById("report-json").textContent = "";
    document.getElementById("copy-status").textContent = "";
  }
}

function show(n) {
  current = Math.max(1, Math.min(TOTAL_STEPS, n));
  steps.forEach(function (s) { s.hidden = parseInt(s.getAttribute("data-step"), 10) !== current; });
  bar.style.width = (current / TOTAL_STEPS * 100) + "%";
  stepLabel.textContent = current + " / " + TOTAL_STEPS;
  prevBtn.disabled = current === 1;
  nextBtn.textContent = current === TOTAL_STEPS ? "완료" : "다음";
  onEnter(current);
}

nextBtn.addEventListener("click", function () {
  if (!validateStep(current)) return;
  if (current < TOTAL_STEPS) show(current + 1);
});
prevBtn.addEventListener("click", function () { if (current > 1) show(current - 1); });

document.getElementById("copy-report").addEventListener("click", function () {
  var status = document.getElementById("copy-status");
  status.textContent = "자료 생성 중...";
  fetchJSON("/report-data", reportPayload()).then(function (res) {
    if (!res.ok || res.body.status !== "ok") {
      status.textContent = res.body.message || "생성 실패";
      return;
    }
    var text = JSON.stringify(res.body.report, null, 2);
    document.getElementById("report-json").textContent = text;
    function done() { status.textContent = "복사되었습니다."; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () {
        status.textContent = "복사 실패 — 아래 내용을 길게 눌러 복사하세요.";
      });
    } else {
      status.textContent = "아래 내용을 길게 눌러 복사하세요.";
    }
  }).catch(function () { status.textContent = "오류가 발생했습니다."; });
});

syncCalendar();
syncTimeStatus();
syncRegion();
show(1);
