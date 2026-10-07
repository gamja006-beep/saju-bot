from flask import Flask, request, jsonify, render_template_string
import os

from saju_engine import compute_saju, SajuInputError

app = Flask(__name__)

INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>사주 입력</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Malgun Gothic", sans-serif;
      background: #f4f5f7;
      color: #222;
      padding: 16px;
    }
    .card {
      max-width: 460px;
      margin: 24px auto;
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.08);
      padding: 24px;
    }
    h1 { font-size: 20px; margin: 0 0 20px; text-align: center; }
    label { display: block; font-size: 14px; margin: 14px 0 6px; font-weight: 600; }
    input, select, button {
      width: 100%;
      padding: 12px;
      font-size: 16px;
      border: 1px solid #ccd0d5;
      border-radius: 8px;
    }
    .radio-row { display: flex; gap: 16px; margin-top: 6px; }
    .radio-row label { display: flex; align-items: center; gap: 6px; margin: 0; font-weight: 400; }
    .radio-row input { width: auto; }
    .check-row { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
    .check-row input { width: auto; }
    .check-row.hidden { display: none; }
    button {
      margin-top: 20px;
      background: #5b6ef5;
      color: #fff;
      border: none;
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: default; }
    #result {
      margin-top: 18px;
      padding: 14px;
      border-radius: 8px;
      background: #eef0fe;
      font-size: 15px;
      line-height: 1.5;
      display: none;
    }
    #result.show { display: block; }
    #result.error { background: #fdecec; }
    table.pillars { width: 100%; border-collapse: collapse; margin: 10px 0; text-align: center; }
    table.pillars th, table.pillars td { border: 1px solid #d5d8ff; padding: 8px 4px; }
    table.pillars th { background: #dfe3ff; font-size: 13px; }
    table.pillars .gz { font-size: 20px; font-weight: 700; }
    table.pillars .ko { font-size: 12px; color: #555; }
    .meta { font-size: 13px; color: #444; margin-top: 8px; }
    .warn { margin-top: 10px; padding: 10px; background: #fff6e0; border-radius: 6px; font-size: 13px; }
    .note { margin-top: 10px; font-size: 12px; color: #777; }
  </style>
</head>
<body>
  <div class="card">
    <h1>사주 입력</h1>
    <form id="saju-form">
      <label>달력</label>
      <div class="radio-row">
        <label><input type="radio" name="calendar" value="solar" checked> 양력</label>
        <label><input type="radio" name="calendar" value="lunar"> 음력</label>
      </div>

      <div class="check-row hidden" id="leap-row">
        <input type="checkbox" id="is_leap_month" name="is_leap_month">
        <label for="is_leap_month" style="margin:0;font-weight:400;">윤달</label>
      </div>

      <label for="birth_date">생년월일</label>
      <input type="date" id="birth_date" name="birth_date" required>

      <label for="birth_time">태어난 시간 (선택)</label>
      <input type="time" id="birth_time" name="birth_time">

      <label for="gender">성별</label>
      <select id="gender" name="gender">
        <option value="남">남</option>
        <option value="여">여</option>
      </select>

      <button type="submit">사주 보기</button>
    </form>
    <div id="result"></div>
  </div>

  <script>
    const form = document.getElementById('saju-form');
    const result = document.getElementById('result');
    const button = form.querySelector('button');
    const leapRow = document.getElementById('leap-row');
    const leapCheck = document.getElementById('is_leap_month');

    function syncCalendar() {
      const isLunar = form.querySelector('input[name="calendar"]:checked').value === 'lunar';
      leapRow.classList.toggle('hidden', !isLunar);
      if (!isLunar) leapCheck.checked = false;
    }
    form.querySelectorAll('input[name="calendar"]').forEach(r => r.addEventListener('change', syncCalendar));
    syncCalendar();

    function esc(s) {
      return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    }
    function pillarCell(p) {
      if (!p) return '<td><span class="gz">-</span></td>';
      return '<td><span class="gz">' + esc(p.ganzhi) + '</span><br><span class="ko">' +
             esc(p.gan_ko + p.zhi_ko) + '</span></td>';
    }
    function render(data) {
      const p = data.pillars;
      let html = '<table class="pillars"><tr><th>연주</th><th>월주</th><th>일주</th><th>시주</th></tr><tr>' +
        pillarCell(p.year) + pillarCell(p.month) + pillarCell(p.day) + pillarCell(p.time) + '</tr></table>';
      html += '<div class="meta">양력 ' + esc(data.solar.year) + '-' +
        String(data.solar.month).padStart(2, '0') + '-' + String(data.solar.day).padStart(2, '0') +
        (data.solar.time ? ' ' + esc(data.solar.time) : '') + '<br>' + esc(data.lunar.text) + '</div>';
      if (!p.time) html += '<div class="note">출생 시간 미입력 — 시주 생략</div>';
      if (data.boundary_warning) {
        const b = data.boundary_warning;
        html += '<div class="warn">' + esc(b.message) +
          '<br>· sect=2(기본): 일 ' + esc(b.sect2.day.ganzhi) + ' / 시 ' + esc(b.sect2.time.ganzhi) +
          '<br>· sect=1(23시 전환): 일 ' + esc(b.sect1.day.ganzhi) + ' / 시 ' + esc(b.sect1.time.ganzhi) + '</div>';
      }
      if (data.accuracy_note) html += '<div class="note">' + esc(data.accuracy_note) + '</div>';
      return html;
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const payload = {
        calendar: form.querySelector('input[name="calendar"]:checked').value,
        is_leap_month: leapCheck.checked,
        birth_date: document.getElementById('birth_date').value,
        birth_time: document.getElementById('birth_time').value,
        gender: document.getElementById('gender').value
      };
      button.disabled = true;
      result.className = 'show';
      result.textContent = '분석 중...';
      try {
        const res = await fetch('/saju', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.status === 'ok') {
          result.className = 'show';
          result.innerHTML = render(data);
        } else {
          result.className = 'show error';
          result.textContent = data.message || '입력을 확인해 주세요.';
        }
      } catch (err) {
        result.className = 'show error';
        result.textContent = '오류가 발생했습니다. 다시 시도해 주세요.';
      } finally {
        button.disabled = false;
      }
    });
  </script>
</body>
</html>
"""


@app.route('/', methods=['GET'])
def index():
    return render_template_string(INDEX_HTML)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/saju', methods=['POST'])
def saju():
    data = request.get_json(silent=True) or {}
    try:
        result = compute_saju(
            calendar=data.get('calendar', 'solar'),
            birth_date=data.get('birth_date'),
            birth_time=data.get('birth_time'),
            gender=data.get('gender'),
            is_leap_month=data.get('is_leap_month', False),
        )
    except SajuInputError as e:
        return jsonify({"status": "error", "code": "invalid_input", "message": str(e)}), 400
    except Exception:
        # 예상하지 못한 내부 오류: 상세 내용을 사용자에게 노출하지 않는다.
        return jsonify({
            "status": "error",
            "code": "internal_error",
            "message": "사주 계산 중 오류가 발생했습니다.",
        }), 500
    return jsonify(result), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
