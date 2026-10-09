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
  { id: "basic_9900", name: "기본 해석", price: "9,900원", kind: "paid", desc: "핵심 명리 해석을 정리해 이메일로 보내 드립니다.", method: "이메일", eta: "3영업일(제안, 운영 확인 전)" },
  { id: "deep_39000", name: "심층 보고서", price: "39,000원", kind: "paid", desc: "주제별 심층 명리 해석을 이메일로 보내 드립니다.", method: "이메일", eta: "5영업일(제안, 운영 확인 전)" },
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
// 상품 비교에서 처음 보여줄 대표 3개(무료/핵심/전문가). 나머지는 '더 보기'로 펼침.
var REPRESENTATIVE_PRODUCTS = { free: true, basic_9900: true, expert_99000: true };

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
  box.appendChild(el("p", "fr-disclaimer", ins.disclaimer));
}

// ---- 공유 카드 (Canvas, 외부 라이브러리 없음, 개인정보 미포함) ----
function homepageUrl() { return window.location.origin; }

function wrapText(ctx, text, x, y, maxWidth, lineHeight) {
  var words = String(text).split(" ");
  var line = "";
  var lines = [];
  for (var i = 0; i < words.length; i++) {
    var test = line ? line + " " + words[i] : words[i];
    if (ctx.measureText(test).width > maxWidth && line) {
      lines.push(line); line = words[i];
    } else { line = test; }
  }
  if (line) lines.push(line);
  for (var j = 0; j < lines.length; j++) ctx.fillText(lines[j], x, y + j * lineHeight);
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
  wrapText(g, ins.persona_title, c.width / 2, 300, c.width - 120, 70);

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
    var badge = pr.kind === "free"
      ? ""
      : "<span class=\"badge email\">담당자 확인 후 이메일</span>";
    var html = "<div class=\"phead\"><span class=\"radio-dot\"></span><span class=\"name\">" +
      esc(pr.name) + "</span><span class=\"price\">" + esc(pr.price) + "</span></div>";
    html += "<div class=\"desc\">" + badge + esc(pr.desc) + "</div>";
    html += "<div class=\"meta\">제공 방식: " + esc(pr.method) + " · 예상: " + esc(pr.eta) + "</div>";
    if (pr.kind !== "free") {
      html += "<button type=\"button\" class=\"pay\" disabled>결제 기능 준비 중</button>";
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
  box.appendChild(row("예상 발송 기간", pr.eta));
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
    if (payCfg.enabled) {
      var banner = payCfg.mode !== "live"
        ? "<p class=\"paid-notice\">테스트 결제입니다. 실제 금액은 차감되지 않습니다.</p>" : "";
      area.innerHTML = banner +
        "<button type=\"button\" id=\"test-pay\" class=\"btn primary\">테스트 결제하기</button>" +
        "<span id=\"pay-status\" class=\"copy-status\" aria-live=\"polite\"></span>";
      document.getElementById("test-pay").addEventListener("click", startPayment);
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
  var status = document.getElementById("pay-status");
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
  prevBtn.disabled = current === 1;
  nextBtn.style.display = current === TOTAL_STEPS ? "none" : "";
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
