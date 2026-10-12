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

// 상품: 실제 결제 미연동(준비 중). 모든 유료 보고서는 명리 해석 도구로 작성한 뒤 담당자가
// 확인하여 이메일로 보낸다(자동 즉시 발송 아님). 무료 명식만 화면에서 즉시 제공.
// 가격은 서버 정본(payments.PRODUCTS)과 일치해야 한다(표시용 문자열).
var PRODUCTS = [
  { id: "free", name: "무료 명식", price: "0원", kind: "free", desc: "출생정보로 사주 4주를 화면에서 바로 확인합니다.", method: "화면", eta: "즉시" },
  { id: "basic_9900", name: "기본 해석", price: "9,900원", kind: "paid", desc: "핵심 명리 해석을 정리해 이메일로 보내 드립니다.", method: "이메일", eta: "3영업일 이내" },
  { id: "deep_39000", name: "심층 보고서", price: "39,000원", kind: "paid", desc: "주제별 심층 명리 해석을 이메일로 보내 드립니다.", method: "이메일", eta: "5영업일 이내" },
  { id: "expert_99000", name: "전문가 보고서", price: "99,000원", kind: "paid", desc: "담당자가 직접 검토해 이메일로 보내 드립니다.", method: "이메일", eta: "영업일 기준(주문 시 안내)" },
  { id: "life_290000", name: "인생설계 보고서", price: "290,000원", kind: "paid", desc: "심층 명리 해석과 비대면 추가질문 1회를 포함해 이메일로 보내 드립니다.", method: "이메일", eta: "영업일 기준(주문 시 안내)" },
  { id: "relation_590000", name: "관계·사업 보고서", price: "590,000원", kind: "paid", desc: "복수 명식 분석과 비대면 추가질문 2회를 포함해 이메일로 보내 드립니다.", method: "이메일", eta: "영업일 기준(주문 시 안내)" },
  { id: "vip_990000", name: "연간 VIP", price: "990,000원", kind: "paid", desc: "연간·분기별 보고서를 이메일로 보내 드립니다.", method: "이메일", eta: "연간·분기별(주문 시 안내)" }
];

// UI 상품 id -> 서버 상품 코드(정본). FREE는 주문 불가.
var PRODUCT_CODE = {
  basic_9900: "BASIC", deep_39000: "DEEP", expert_99000: "EXPERT",
  life_290000: "LIFE_DESIGN", relation_590000: "RELATION_BUSINESS", vip_990000: "ANNUAL_VIP"
};

var TOTAL_STEPS = 8;
var current = 1;
var lastSaju = null;
var lastInsight = null;
var selectedProduct = "free";

// 공유 카드에 들어가는 공개 정보(개인정보 아님).
var SERVICE_NAME = "행운 사주풀이";
// 유료 보고서 전달 안내(상품 카드·주문 확인에서 동일 문구 사용).
// 즉시 자동 발송이나 특정 자격 보유자의 검토를 주장하지 않는다.
var DELIVERY_NOTE = "명식 확인·해석·검토 후 이메일로 보내 드려 시간이 걸립니다(즉시 자동 발송 아님).";
// 1차 출시 대상(무료/기본/심층)을 먼저 보여 준다. 나머지(보류 상품)는 '더 보기'로 펼친다.
var REPRESENTATIVE_PRODUCTS = { free: true, basic_9900: true, deep_39000: true };
// 보류 상품(1차 출시 제외): 구매 가능한 것처럼 보이지 않도록 '준비 중'으로 표시한다.
var HELD_PRODUCTS = { expert_99000: true, life_290000: true, relation_590000: true, vip_990000: true };

// 안전한 DOM 생성 헬퍼: 동적 텍스트는 항상 textContent 로만 넣는다(XSS 방지).
function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

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
function productById(id) {
  for (var i = 0; i < PRODUCTS.length; i++) if (PRODUCTS[i].id === id) return PRODUCTS[i];
  return PRODUCTS[0];
}
function isPaid(id) { return id !== "free"; }
// live 모드에서 서버가 아직 준비되지 않았다고 알린 상품(현재 신청 불가). test 모드는 항상 빈 목록.
function isHeldLive(id) {
  var held = (window.PAY_CONFIG && window.PAY_CONFIG.heldProducts) || [];
  return held.indexOf(PRODUCT_CODE[id]) >= 0;
}
// 전달기한 표시의 단일 출처: 서버가 확정한 기한(deliveryEtas)이 있으면 그 값을, 없으면 상품의
// 안내용 문구(제안)를 쓴다. 상품 카드와 주문 안내가 이 함수를 함께 사용해 불일치를 막는다.
function etaText(pr) {
  var map = (window.PAY_CONFIG && window.PAY_CONFIG.deliveryEtas) || {};
  return map[PRODUCT_CODE[pr.id]] || pr.eta;
}

// ---- 이메일 ----
var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
function emailValue() { return (val("email") || "").trim(); }
function validEmail(e) { return e.length > 0 && e.length <= 254 && EMAIL_RE.test(e); }
function maskEmail(e) {
  var at = e.indexOf("@");
  if (at < 1) return "***";
  var local = e.slice(0, at), dom = e.slice(at);
  var shown = local.slice(0, Math.min(2, local.length));
  return shown + "***" + dom;
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
  return null;
}
function timeStatusLabel() {
  return { exact: "정확", approx: "대략", unknown: "모름" }[checked("time_status")] || "-";
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

function pad2(n) { return String(n).padStart(2, "0"); }

function pillarCell(p) {
  var td = document.createElement("td");
  if (!p) { td.appendChild(el("span", "gz", "-")); return td; }
  td.appendChild(el("span", "gz", p.ganzhi));
  td.appendChild(el("span", "ko", (p.gan_ko || "") + (p.zhi_ko || "")));
  return td;
}

// 명식(한자) 표. 서버 응답 값은 모두 textContent 로만 삽입한다(innerHTML 미사용).
function renderMyeongsikInto(box, data) {
  box.textContent = "";
  var p = data.pillars;
  var table = el("table", "pillars");
  var head = document.createElement("tr");
  ["연주", "월주", "일주", "시주"].forEach(function (t) { head.appendChild(el("th", null, t)); });
  var body = document.createElement("tr");
  [p.year, p.month, p.day, p.time].forEach(function (x) { body.appendChild(pillarCell(x)); });
  table.appendChild(head);
  table.appendChild(body);
  box.appendChild(table);
  var sd = data.solar || {};
  var solarLine = "양력 " + sd.year + "-" + pad2(sd.month) + "-" + pad2(sd.day) + (sd.time ? " " + sd.time : "");
  box.appendChild(el("div", "meta", solarLine));
  box.appendChild(el("div", "meta", (data.lunar && data.lunar.text) || ""));
  var tc = data.time_correction || {};
  box.appendChild(el("div", "meta", "시간·지역 보정: " + (tc.applied ? "적용됨" : "미적용")));
  // 서버가 내려주는 정확도 안내를 그대로 보여 준다(전체 정확성 검증 완료로 표시하지 않는다).
  if (data.accuracy_note) box.appendChild(el("p", "fr-warn", data.accuracy_note));
}

// ---- 무료 사주 요약 화면 ----
function renderElements(elements) {
  var wrap = el("div", "fr-elements");
  var order = ["목", "화", "토", "금", "수"];
  var max = 1;
  order.forEach(function (k) { if ((elements[k] || 0) > max) max = elements[k]; });
  order.forEach(function (k) {
    var n = elements[k] || 0;
    var r = el("div", "fr-el-row");
    r.appendChild(el("span", "fr-el-k", k));
    var barWrap = el("span", "fr-el-bar");
    var fill = el("span", "fr-el-fill");
    fill.style.width = Math.round(n / max * 100) + "%";
    barWrap.appendChild(fill);
    r.appendChild(barWrap);
    r.appendChild(el("span", "fr-el-n", String(n)));
    wrap.appendChild(r);
  });
  return wrap;
}

function renderFreeResult(saju, ins) {
  var box = document.getElementById("free-result");
  box.innerHTML = "";  // 초기화. 이후 모든 동적 값은 textContent 로만 삽입한다.

  var aliasName = (ins.alias && ins.alias.length) ? ins.alias : "당신";

  box.appendChild(el("h3", "fr-title", aliasName + "님의 사주 한 장 요약"));
  box.appendChild(el("p", "fr-label", ins.reference_label));
  box.appendChild(el("p", "fr-persona", ins.persona_sentence));

  box.appendChild(el("h4", "fr-h", "타고난 강점"));
  var ul = el("ul", "fr-strength");
  ins.strengths.forEach(function (s) { ul.appendChild(el("li", null, s)); });
  box.appendChild(ul);

  box.appendChild(el("h4", "fr-h", "사람들이 느끼는 모습"));
  box.appendChild(el("p", "fr-p", ins.others_view));

  box.appendChild(el("h4", "fr-h", "관계 성향"));
  box.appendChild(el("p", "fr-p", ins.relationship));

  box.appendChild(el("h4", "fr-h", "조심하면 좋은 점"));
  box.appendChild(el("p", "fr-p", ins.caution));

  box.appendChild(el("h4", "fr-h", "오늘부터 적용할 제안"));
  box.appendChild(el("p", "fr-p", ins.suggestion));

  if (ins.topic_previews && ins.topic_previews.length) {
    box.appendChild(el("h4", "fr-h", "관심 주제 미리보기"));
    ins.topic_previews.forEach(function (tp) {
      var card = el("div", "fr-topic");
      card.appendChild(el("div", "fr-topic-name", tp.topic));
      tp.lines.forEach(function (ln) { card.appendChild(el("p", "fr-p", ln)); });
      box.appendChild(card);
    });
    var lock = el("div", "fr-lock");
    lock.appendChild(el("span", "fr-lock-ico", "🔒"));
    lock.appendChild(el("span", "fr-lock-txt", ins.paid_hint));
    box.appendChild(lock);
  }

  box.appendChild(el("h4", "fr-h", "사주 명식 (연·월·일·시)"));
  var ms = el("div", "myeongsik");
  renderMyeongsikInto(ms, saju);
  box.appendChild(ms);
  if (ins.notes && ins.notes.length) {
    ins.notes.forEach(function (nt) { box.appendChild(el("p", "fr-warn", nt)); });
  }

  box.appendChild(el("h4", "fr-h", "오행 분포"));
  box.appendChild(renderElements(ins.elements));
  box.appendChild(el("p", "fr-note", ins.element_note));

  box.appendChild(buildShareArea(ins));
  box.appendChild(buildUpsellArea(ins));
  box.appendChild(el("p", "fr-disclaimer", ins.disclaimer));
}

// ---- 공유 카드 (Canvas, 외부 라이브러리 없음, 개인정보 미포함) ----
function homepageUrl() { return window.location.origin; }

// 제목을 폭에 맞춰 최대 2줄로 균형 있게 나눈다. 한 줄에 한 글자만 남기지 않고,
// 공백(어절) 경계를 우선 사용하되 없으면 글자 단위로 끊는다. 한 줄에 들어가면 1줄 그대로.
function titleLines(ctx, text, maxWidth) {
  text = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
  if (!text || ctx.measureText(text).width <= maxWidth) return [text];
  var spaceC = [], charC = [];
  for (var i = 1; i < text.length; i++) {
    var l1 = text.slice(0, i).trim(), l2 = text.slice(i).trim();
    if (l1.length < 2 || l2.length < 2) continue;              // 한 줄에 한 글자만 남기지 않음
    var w1 = ctx.measureText(l1).width, w2 = ctx.measureText(l2).width;
    if (w1 > maxWidth || w2 > maxWidth) continue;              // 각 줄이 폭을 넘지 않음
    var cand = { l1: l1, l2: l2, diff: Math.abs(w1 - w2) };
    if (text.charAt(i - 1) === " " || text.charAt(i) === " ") spaceC.push(cand);
    else charC.push(cand);
  }
  var pool = spaceC.length ? spaceC : charC;                   // 어절 경계 우선
  if (!pool.length) {                                          // 2줄로 못 담으면 글자 단위 그리디
    var cut = text.length;
    for (var j = 1; j <= text.length; j++) {
      if (ctx.measureText(text.slice(0, j)).width > maxWidth) { cut = j - 1; break; }
    }
    cut = Math.max(1, cut);
    return [text.slice(0, cut), text.slice(cut)];
  }
  pool.sort(function (a, b) { return a.diff - b.diff; });      // 두 줄 폭이 가장 비슷한 분할
  return [pool[0].l1, pool[0].l2];
}
function drawTitle(ctx, text, cx, maxWidth, centerY, lineHeight) {
  var lines = titleLines(ctx, text, maxWidth);
  var startY = centerY - (lines.length - 1) * lineHeight / 2;  // 줄 수에 맞춰 세로 중앙 배치
  for (var i = 0; i < lines.length; i++) ctx.fillText(lines[i], cx, startY + i * lineHeight);
  return lines.length;
}

// 공유 카드에는 persona_title / keywords / 서비스명 / 공개 홈페이지 URL / 안내문구만 그린다.
// 본명·별칭·생년월일·시간·지역·성별·이메일·명식·질문·주문정보는 절대 포함하지 않는다.
function drawShareCard(ins) {
  var c = document.createElement("canvas");
  c.width = 720; c.height = 1280;
  var g = c.getContext("2d");
  var grad = g.createLinearGradient(0, 0, 0, c.height);
  grad.addColorStop(0, "#1B2342"); grad.addColorStop(1, "#0E1220");
  g.fillStyle = grad; g.fillRect(0, 0, c.width, c.height);
  g.textAlign = "center";

  g.fillStyle = "rgba(255,255,255,.85)";
  g.font = "600 36px -apple-system, 'Malgun Gothic', sans-serif";
  g.fillText("나의 사주 키워드", c.width / 2, 170);

  g.fillStyle = "#ffffff";
  g.font = "800 56px -apple-system, 'Malgun Gothic', sans-serif";
  drawTitle(g, ins.persona_title, c.width / 2, c.width - 120, 330, 70);

  var keywords = ins.keywords || [];
  var ky = 560;
  g.font = "700 40px -apple-system, 'Malgun Gothic', sans-serif";
  for (var i = 0; i < keywords.length; i++) {
    var text = "# " + keywords[i];
    var w = g.measureText(text).width + 56;
    var x = (c.width - w) / 2;
    var yy = ky + i * 110;
    g.fillStyle = "rgba(255,255,255,.18)";
    if (g.roundRect) { g.beginPath(); g.roundRect(x, yy, w, 78, 39); g.fill(); }
    else { g.fillRect(x, yy, w, 78); }
    g.fillStyle = "#ffffff";
    g.fillText(text, c.width / 2, yy + 53);
  }

  g.fillStyle = "#ffffff";
  g.font = "700 40px -apple-system, 'Malgun Gothic', sans-serif";
  g.fillText("당신의 사주 키워드는?", c.width / 2, 1050);

  g.fillStyle = "rgba(255,255,255,.95)";
  g.font = "700 34px -apple-system, 'Malgun Gothic', sans-serif";
  g.fillText(SERVICE_NAME, c.width / 2, 1150);
  g.fillStyle = "rgba(255,255,255,.85)";
  g.font = "400 26px -apple-system, 'Malgun Gothic', sans-serif";
  g.fillText(homepageUrl(), c.width / 2, 1195);
  return c;
}

function shareStatusEl() { return document.getElementById("share-status"); }

function triggerDownload(url, name) {
  var a = el("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function saveShareImage(ins) {
  var c = drawShareCard(ins);
  var status = shareStatusEl();
  if (c.toBlob) {
    c.toBlob(function (blob) {
      var url = URL.createObjectURL(blob);
      triggerDownload(url, "saju-keyword.png");
      if (status) status.textContent = "이미지를 저장했어요.";
    }, "image/png");
  } else {
    triggerDownload(c.toDataURL("image/png"), "saju-keyword.png");
    if (status) status.textContent = "이미지를 저장했어요.";
  }
}

function copyLink() {
  var status = shareStatusEl();
  var url = homepageUrl();  // 공개 홈페이지 주소만. 고객정보/명식정보는 넣지 않는다.
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(url).then(function () {
      if (status) status.textContent = "링크를 복사했어요.";
    }).catch(function () { fallbackCopy(url, status); });
  } else { fallbackCopy(url, status); }
}

function fallbackCopy(text, status) {
  var ta = el("textarea");
  ta.value = text; ta.setAttribute("readonly", "");
  ta.style.position = "absolute"; ta.style.left = "-9999px";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); if (status) status.textContent = "링크를 복사했어요."; }
  catch (e) { if (status) status.textContent = text; }
  document.body.removeChild(ta);
}

function shareResult(ins) {
  var status = shareStatusEl();
  var text = "나의 사주 키워드 — " + ins.persona_title + " | " + SERVICE_NAME;
  var url = homepageUrl();
  var c = drawShareCard(ins);
  if (navigator.share && c.toBlob) {
    c.toBlob(function (blob) {
      try {
        var file = new File([blob], "saju-keyword.png", { type: "image/png" });
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
          navigator.share({ files: [file], text: text, url: url }).catch(function () {});
          return;
        }
      } catch (e) { /* File 미지원 → 아래로 */ }
      navigator.share({ text: text, url: url }).catch(function () { saveShareImage(ins); });
    }, "image/png");
  } else if (navigator.share) {
    navigator.share({ text: text, url: url }).catch(function () { copyLink(); });
  } else {
    saveShareImage(ins);  // 공유 미지원 → PNG 저장으로 대체
  }
}

function buildShareArea(ins) {
  var wrap = el("div", "fr-share");
  wrap.appendChild(el("h4", "fr-h", "결과 공유하기"));
  wrap.appendChild(el("p", "fr-note", "개인정보 없이 '사주 키워드'만 담은 이미지예요."));
  var btns = el("div", "fr-share-btns");
  var bSave = el("button", "btn ghost fr-btn", "결과 이미지 저장");
  var bShare = el("button", "btn primary fr-btn", "친구에게 공유");
  var bLink = el("button", "btn ghost fr-btn", "링크 복사");
  bSave.type = "button"; bShare.type = "button"; bLink.type = "button";
  bSave.addEventListener("click", function () { saveShareImage(ins); });
  bShare.addEventListener("click", function () { shareResult(ins); });
  bLink.addEventListener("click", function () { copyLink(); });
  btns.appendChild(bSave); btns.appendChild(bShare); btns.appendChild(bLink);
  wrap.appendChild(btns);
  var st = el("span", "copy-status");
  st.id = "share-status";
  st.setAttribute("aria-live", "polite");
  wrap.appendChild(st);
  return wrap;
}

// ---- 무료 → 유료 전환 안내(상품 범위 예시 질문 + BASIC/DEEP 비교) ----
// 고객이 고른 관심 주제를 보고서의 '실제 제공 범위'와 연결해 보여 준다. 확정적 예언·불안 조장
// 없이, 아직 개인별로 분석한 결과가 아니라 '보고서에서 다루는 범위 예시'임을 분명히 한다.
var TOPIC_PHRASE = {
  "재물": "재물·돈 관리", "직업": "일·직업", "애정": "애정·관계",
  "건강": "건강·생활 리듬", "학업": "학업·공부", "가족": "가족 관계"
};
function dominantElement(ins) {
  var e = ins.elements || {};
  var order = ["목", "화", "토", "금", "수"];
  var best = order[0], max = -1;
  order.forEach(function (k) { if ((e[k] || 0) > max) { max = e[k] || 0; best = k; } });
  return best;
}
function topicQuestions(ins) {
  var elm = dominantElement(ins);
  var topics = topicsSelected();
  var qs = [];
  if (topics.length) {
    var p0 = TOPIC_PHRASE[topics[0]] || topics[0];
    qs.push(elm + " 기운이 두드러진다는 설명이 제 " + p0 + "에는 어떻게 연결될까요?");
    var p1 = topics.length > 1 ? (TOPIC_PHRASE[topics[1]] || topics[1]) : p0;
    qs.push("강점으로 나온 성향을 " + p1 + "에서 어떻게 살리면 좋을까요?");
    if (topics.length > 2) {
      var p2 = TOPIC_PHRASE[topics[2]] || topics[2];
      qs.push("조심하면 좋은 점이 " + p2 + "에서는 어떻게 나타날 수 있을까요?");
    }
  } else {
    qs.push(elm + " 기운이 두드러진다는 설명이 제 일과 관계에는 어떻게 연결될까요?");
    qs.push("강점과 조심할 점을 일상에서 어떻게 적용하면 좋을까요?");
  }
  return qs.slice(0, 3);
}
// 비교 카드 전달기한: 서버가 확정한 값(deliveryEtas)만 쓴다. 환경변수(기한)가 없으면
// 날짜를 임의로 확정해 표시하지 않고 '주문 시 안내'로 둔다.
function cmpEta(pr) {
  var map = (window.PAY_CONFIG && window.PAY_CONFIG.deliveryEtas) || {};
  return map[PRODUCT_CODE[pr.id]] || "주문 시 안내";
}
// 비교 카드의 '선택' → 기존 상품 선택 단계(7)로 이동한다. 이메일 확인·국외이전 동의·
// 결제 직전 확인 단계를 건너뛰지 않는다(선택값과 입력은 그대로 유지).
function selectProductAndGo(id) {
  selectedProduct = id;
  started = true;
  show(7);
}
function cmpCard(pr, cls, scope, selectLabel) {
  var card = el("div", "cmp-card " + cls);
  var art = document.createElement("img");
  art.className = "cmp-art";
  art.src = pr.id === "basic_9900" ? "/static/basic-report.webp" : "/static/deep-report.webp";
  art.alt = "";  // 상품명·설명 바로 아래에 있으므로 그림은 장식으로 처리한다.
  art.loading = "lazy";
  art.decoding = "async";
  card.appendChild(art);
  var head = el("div", "cmp-head");
  head.appendChild(el("span", "cmp-name", pr.name));
  head.appendChild(el("span", "cmp-price", pr.price));
  card.appendChild(head);
  card.appendChild(el("div", "cmp-eta", "예상 전달: " + cmpEta(pr)));
  var ul = el("ul", "cmp-scope");
  scope.forEach(function (s) { ul.appendChild(el("li", null, s)); });
  card.appendChild(ul);
  card.appendChild(el("p", "cmp-note", DELIVERY_NOTE));
  var btn = el("button", "btn primary cmp-btn", selectLabel);
  btn.type = "button";
  if (isHeldLive(pr.id)) {  // 보류(현재 신청 불가) 상품은 선택 가능해 보이지 않게 비활성화
    btn.disabled = true;
    btn.textContent = "현재 신청 불가";
  } else {
    btn.addEventListener("click", function () { selectProductAndGo(pr.id); });
  }
  card.appendChild(btn);
  return card;
}
function buildUpsellArea(ins) {
  var wrap = el("div", "fr-upsell");
  wrap.appendChild(el("h4", "fr-upsell-title", "여기까지 무료로 확인했어요"));
  wrap.appendChild(el("p", "fr-note",
    "무료 요약은 여기까지예요. 아래는 유료 보고서에서 더 깊이 다루는 범위와 예시 질문이에요."));

  wrap.appendChild(el("h4", "fr-h", "보고서에서 더 살펴볼 수 있는 질문"));
  var qul = el("ul", "fr-q");
  topicQuestions(ins).forEach(function (q) { qul.appendChild(el("li", null, q)); });
  wrap.appendChild(qul);
  wrap.appendChild(el("p", "fr-note",
    "위 질문은 보고서에서 다루는 범위를 보여 주는 예시예요. 아직 개인별로 분석한 답이 아니에요."));

  var cmp = el("div", "cmp");
  cmp.appendChild(cmpCard(productById("basic_9900"), "cmp-basic", [
    "명식(사주 네 기둥) 근거부터 차근차근 설명",
    "타고난 성향과 강점 정리",
    "관계·가족에서의 성향",
    "오늘부터 해 볼 실천 제안",
    "연도별 장기 운세는 이 상품 범위가 아니에요"
  ], "기본 해석 선택"));
  cmp.appendChild(cmpCard(productById("deep_39000"), "cmp-deep", [
    "기본 해석을 모두 포함",
    "고른 관심 주제별로 더 자세히 분석",
    "직접 쓴 질문·현재 상황을 명식 근거와 연결해 설명",
    "상황에 맞춘 구체적인 현실 적용 제안",
    "기간을 적었다면 그 기간의 흐름도 함께(안 적으면 임의로 만들지 않아요)"
  ], "심층 보고서 선택"));
  wrap.appendChild(cmp);
  return wrap;
}

function syncPaidExtra() {
  document.getElementById("paid-extra").hidden = !isPaid(selectedProduct);
}

function renderProducts() {
  var box = document.getElementById("products");
  box.innerHTML = "";
  PRODUCTS.forEach(function (pr) {
    var div = document.createElement("div");
    div.className = "product" + (pr.kind === "free" ? " free" : "");
    div.setAttribute("role", "radio");
    div.setAttribute("tabindex", "0");
    div.setAttribute("aria-checked", pr.id === selectedProduct ? "true" : "false");
    var held = HELD_PRODUCTS[pr.id] === true;
    if (held) div.classList.add("held");
    var badge = pr.kind === "free" ? ""
      : (held ? "<span class=\"badge soon-badge\">준비 중</span>"
              : "<span class=\"badge email\">담당자 확인 후 이메일</span>");
    var html = "<div class=\"phead\"><span class=\"radio-dot\"></span><span class=\"name\">" +
      esc(pr.name) + "</span><span class=\"price\">" + esc(pr.price) + "</span></div>";
    if (pr.id === "basic_9900" || pr.id === "deep_39000") {
      var artPath = pr.id === "basic_9900" ? "basic-report.webp" : "deep-report.webp";
      html = "<img class=\"product-art\" src=\"/static/" + artPath + "\" alt=\"\" loading=\"lazy\" decoding=\"async\">" + html;
    }
    html += "<div class=\"desc\">" + badge + esc(pr.desc) + "</div>";
    html += "<div class=\"meta\">제공 방식: " + esc(pr.method) + " · 예상: " + esc(etaText(pr)) + "</div>";
    if (pr.kind !== "free") {
      html += "<div class=\"meta\">" + esc(DELIVERY_NOTE) + "</div>";
      if (held) {
        html += "<button type=\"button\" class=\"pay\" disabled>준비 중 · 현재 신청 불가</button>";
      } else if (isHeldLive(pr.id)) {
        html += "<button type=\"button\" class=\"pay\" disabled>현재 신청 불가</button>";
      } else {
        html += "<button type=\"button\" class=\"pay\" disabled>결제 기능 준비 중</button>";
      }
    }
    div.innerHTML = html;
    // 대표 3개(무료/핵심/전문가)만 우선 표시, 나머지는 '더 보기'로 펼친다. 7개 모두 DOM 유지.
    if (!REPRESENTATIVE_PRODUCTS[pr.id]) {
      div.classList.add("product-extra");
      div.hidden = true;
    }
    function pick() {
      selectedProduct = pr.id;
      Array.prototype.forEach.call(box.querySelectorAll(".product"), function (c) {
        c.classList.remove("selected");
        c.setAttribute("aria-checked", "false");
      });
      div.classList.add("selected");
      div.setAttribute("aria-checked", "true");
      syncPaidExtra();
      setError("");
    }
    div.addEventListener("click", pick);
    div.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
    });
    box.appendChild(div);
  });
  // 초기 선택 반영(상품 카드만 대상)
  Array.prototype.forEach.call(box.querySelectorAll(".product"), function (c, i) {
    if (PRODUCTS[i].id === selectedProduct) c.classList.add("selected");
  });
  // '프리미엄 보고서 더 보기' 토글
  var toggle = el("button", "btn ghost fr-more", "프리미엄 보고서 더 보기");
  toggle.type = "button";
  toggle.addEventListener("click", function () {
    var extras = box.querySelectorAll(".product-extra");
    var hide = extras.length && extras[0].hidden === false;  // 현재 펼쳐져 있으면 접는다
    Array.prototype.forEach.call(extras, function (c) { c.hidden = hide; });
    toggle.textContent = hide ? "프리미엄 보고서 더 보기" : "간단히 보기";
  });
  box.appendChild(toggle);
  syncPaidExtra();
}

// ---- 8단계: 고객용 신청 내용 확인 ----
function row(label, value) {
  var r = el("div", "srow");
  r.appendChild(el("span", "sk", label));
  r.appendChild(el("span", "sv", value));
  return r;
}
function renderConfirm() {
  var box = document.getElementById("confirm");
  var pr = productById(selectedProduct);
  var cal = checked("calendar") === "lunar" ? "음력" : "양력";
  var leap = form.elements["is_leap_month"].checked ? " (윤달)" : "";
  var birth = cal + " " + (val("birth_date") || "-") + leap + " · " + checked("gender");
  var city = cityInfo();
  var region = city.lon === null ? "모름(미보정)" : city.name;
  var topics = topicsSelected();

  box.textContent = "";
  box.appendChild(row("별칭", val("alias") || "(미입력)"));
  box.appendChild(row("상담 유형", checked("consultation_type")));
  box.appendChild(row("출생 정보", birth));
  box.appendChild(row("출생시간 상태", timeStatusLabel()));
  box.appendChild(row("출생 지역", region));
  box.appendChild(row("관심 주제", topics.length ? topics.join(", ") : "(선택 안 함)"));
  box.appendChild(row("선택 상품", pr.name + " · " + pr.price));
  box.appendChild(row("제공 방식", pr.method));
  box.appendChild(row("예상 발송 기간", etaText(pr)));
  if (isPaid(selectedProduct)) {
    box.appendChild(el("p", "fr-note", DELIVERY_NOTE));
  }
  if (isPaid(selectedProduct)) {
    box.appendChild(row("수령 이메일", maskEmail(emailValue())));
  }

  if (lastSaju && lastSaju.pillars) {
    var p = lastSaju.pillars;
    var four = [p.year, p.month, p.day, p.time].map(function (x) { return x ? x.ganzhi : "미상"; }).join(" ");
    var gzRow = row("무료 명식", four);
    gzRow.lastChild.className = "sv gz-line";
    box.appendChild(gzRow);
  }

  // 완료 영역
  var area = document.getElementById("complete-area");
  var payCfg = window.PAY_CONFIG || { enabled: false, mode: "test" };
  if (isPaid(selectedProduct)) {
    if (isHeldLive(selectedProduct)) {
      // 서버가 준비 안 됐다고 알린 상품: 결제 진입 차단(서버도 503 으로 거부).
      area.innerHTML = "<p class=\"paid-notice\">선택하신 상품은 현재 신청 불가입니다. 다른 상품을 선택해 주세요.</p>" +
        "<button type=\"button\" class=\"btn primary\" disabled>현재 신청 불가</button>";
    } else if (payCfg.enabled) {
      var banner = payCfg.mode !== "live"
        ? "<p class=\"paid-notice\">테스트 결제입니다. 실제 금액은 차감되지 않습니다.</p>" : "";
      // 국외 이전(OpenAI·Google) 별도 동의: 보고서 작성·발송은 개인정보 국외 이전을 수반하므로
      // 결제 전 동의를 받고, 동의 전에는 결제 버튼을 비활성화한다(라이브 서버도 동의 없으면 거부).
      var consent =
        "<label class=\"consent-box\"><input type=\"checkbox\" id=\"agree-cross-border\"> " +
        "<span>[필수] 보고서 작성·발송을 위해 생년월일·질문 등 개인정보가 " +
        "OpenAI OpCo, LLC(미국)·Google LLC(미국 등)로 국외 이전되는 데 동의합니다. " +
        "항목·목적·시기·방법·보유·거부 방법은 <a href=\"/privacy\" target=\"_blank\" rel=\"noopener\">개인정보처리방침</a>을 " +
        "확인하세요. 동의하지 않으면 유료 보고서를 진행할 수 없습니다.</span></label>";
      area.innerHTML = banner + consent +
        "<button type=\"button\" id=\"test-pay\" class=\"btn primary\" disabled>테스트 결제하기</button>" +
        "<span id=\"pay-status\" class=\"copy-status\" aria-live=\"polite\"></span>";
      var agree = document.getElementById("agree-cross-border");
      var payBtn = document.getElementById("test-pay");
      agree.addEventListener("change", function () { payBtn.disabled = !agree.checked; });
      payBtn.addEventListener("click", startPayment);
    } else {
      area.innerHTML = "<p class=\"paid-notice\">현재 결제 기능 준비 중이며 아직 주문이 접수되지 않습니다.</p>" +
        "<button type=\"button\" class=\"btn primary\" disabled>결제 기능 준비 중</button>";
    }
  } else {
    // 무료: 중복 완료 버튼/문구 없이, 요약 화면으로 안내만 한다.
    area.innerHTML = "";
    area.appendChild(el("p", "fr-note", "무료 사주 요약은 '6단계 무료 사주 요약'에서 다시 보고 공유할 수 있어요."));
  }
}

function orderPayload() {
  var p = basePayload();
  p.product_code = PRODUCT_CODE[selectedProduct] || "";
  p.email = emailValue();
  p.alias = val("alias");
  p.birth_time_status = checked("time_status");  // exact | approx | unknown
  p.consultation_type = checked("consultation_type");
  p.topics = topicsSelected();
  p.question = val("question");
  p.situation = val("situation");
  p.target_period = val("target_period");
  var agree = document.getElementById("agree-cross-border");
  p.agree_cross_border = !!(agree && agree.checked);  // 국외 이전 동의 여부
  return p;
}

// 콘솔에는 진단용 code/message만 남긴다. 비밀값·이메일·주문자료는 절대 출력하지 않는다.
function logPayError(e) {
  if (window.console && console.error) {
    var err = e || {};
    console.error("[pay] requestPayment 실패", { code: err.code || "", message: err.message || "" });
  }
}

// 결제 활성 환경(테스트)에서만 호출된다. 토스 SDK v2 결제창형, 비회원 ANONYMOUS.
function startPayment() {
  if (isHeldLive(selectedProduct)) { return; }  // 방어적 차단(버튼은 이미 비활성)
  var status = document.getElementById("pay-status");
  var agree = document.getElementById("agree-cross-border");
  if (agree && !agree.checked) {  // 방어적 차단(버튼은 이미 비활성)
    status.textContent = "개인정보 국외 이전 동의가 필요합니다.";
    return;
  }
  status.textContent = "주문 생성 중...";
  fetchJSON("/api/orders", orderPayload()).then(function (res) {
    if (!res.ok || res.body.status !== "ok") {
      status.textContent = res.body.message || "주문을 생성하지 못했습니다.";
      return;
    }
    var o = res.body;
    if (typeof TossPayments === "undefined") { status.textContent = "결제 모듈을 불러오지 못했습니다."; return; }
    function onPayError(e) {
      logPayError(e);
      status.textContent = "결제를 시작하지 못했습니다.";
    }
    try {
      var tp = TossPayments(window.PAY_CONFIG.clientKey);
      var payment = tp.payment({ customerKey: TossPayments.ANONYMOUS });
      // v2 requestPayment 는 Promise 를 반환한다. 동기 throw 와 비동기 reject 를 모두 처리한다.
      var req = payment.requestPayment({
        method: "CARD",
        amount: { currency: "KRW", value: o.amount },
        orderId: o.orderId,
        orderName: o.orderName,
        customerEmail: emailValue(),
        successUrl: window.location.origin + "/payment/success",
        failUrl: window.location.origin + "/payment/fail"
      });
      if (req && typeof req.then === "function") { req.catch(onPayError); }
    } catch (e) {
      onPayError(e);
    }
  }).catch(function () { status.textContent = "오류가 발생했습니다."; });
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
  if (n === 7) {
    if (isPaid(selectedProduct)) {
      var e = emailValue();
      if (!e) { setError("유료 보고서를 받을 이메일을 입력해 주세요."); return false; }
      if (e.length > 254) { setError("이메일은 254자 이하여야 합니다."); return false; }
      if (!validEmail(e)) { setError("이메일 형식이 올바르지 않습니다. 예: you@example.com"); return false; }
    }
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

function freeResultPayload() {
  var p = basePayload();
  p.alias = val("alias");
  p.topics = topicsSelected();
  return p;
}

function onEnter(n) {
  if (n === 6) {
    var box = document.getElementById("free-result");
    box.textContent = "결과를 준비하고 있어요...";
    fetchJSON("/free-insights", freeResultPayload()).then(function (res) {
      if (res.ok && res.body.status === "ok") {
        lastSaju = res.body.saju;
        lastInsight = res.body.insight;
        renderFreeResult(res.body.saju, res.body.insight);
      } else {
        box.textContent = res.body.message || "입력을 확인해 주세요.";
      }
    }).catch(function () { box.textContent = "오류가 발생했습니다."; });
  } else if (n === 7) {
    renderProducts();
  } else if (n === 8) {
    renderConfirm();
  }
}

var started = false;  // 사용자가 단계를 직접 넘긴 뒤부터 포커스를 옮긴다(첫 로드 제외).

function show(n) {
  current = Math.max(1, Math.min(TOTAL_STEPS, n));
  steps.forEach(function (s) { s.hidden = parseInt(s.getAttribute("data-step"), 10) !== current; });
  bar.style.width = (current / TOTAL_STEPS * 100) + "%";
  stepLabel.textContent = current + " / " + TOTAL_STEPS;
  var phaseLabel = document.getElementById("form-phase-label");
  if (phaseLabel) phaseLabel.textContent = current <= 5 ? "무료 사주 입력" :
    current === 6 ? "무료 결과" : "보고서 선택";
  var phaseSub = document.getElementById("form-sub");
  if (phaseSub) phaseSub.textContent = current === 1 ?
    "먼저 상담 유형을 고르고 다음 단계에서 출생정보를 입력하세요." :
    current <= 5 ? "출생정보와 궁금한 내용을 입력해 주세요." :
    current === 6 ? "입력한 정보로 계산한 무료 요약입니다." :
    current === 7 ? "무료 결과를 살펴본 뒤 필요한 보고서를 고르세요." :
    "입력 내용을 확인해 주세요.";
  prevBtn.disabled = current === 1;
  nextBtn.style.display = current === TOTAL_STEPS ? "none" : "";
  // 무료 결과(6)·상품 선택(7)은 데스크톱에서 넓고 중앙에 배치한다(입력 단계는 그대로).
  var hero = document.querySelector(".hero");
  if (hero) hero.classList.toggle("result-wide", current === 6 || current === 7);
  onEnter(current);
  if (started) {
    var heading = steps[current - 1].querySelector("h2");
    if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus({ preventScroll: true }); }
    var anchor = document.getElementById("start");
    if (anchor && anchor.scrollIntoView) anchor.scrollIntoView({ block: "start" });
  }
}

nextBtn.addEventListener("click", function () {
  if (!validateStep(current)) return;
  if (current < TOTAL_STEPS) { started = true; show(current + 1); }
});
prevBtn.addEventListener("click", function () { if (current > 1) { started = true; show(current - 1); } });

// 큰 글씨: 화면에서만 바꾼다(브라우저 저장소 미사용, 새로고침하면 기본 크기).
var largeToggle = document.getElementById("large-toggle");
if (largeToggle) {
  largeToggle.addEventListener("click", function () {
    var on = !document.documentElement.classList.contains("large");
    document.documentElement.classList.toggle("large", on);
    largeToggle.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

syncCalendar();
syncTimeStatus();
syncRegion();
show(1);
