from flask import Flask, request, jsonify, render_template_string
import os

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
      max-width: 420px;
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
      white-space: pre-wrap;
      display: none;
    }
    #result.show { display: block; }
  </style>
</head>
<body>
  <div class="card">
    <h1>사주 입력</h1>
    <form id="saju-form">
      <label for="birth_date">생년월일</label>
      <input type="date" id="birth_date" name="birth_date" required>

      <label for="birth_time">태어난 시간</label>
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

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const payload = {
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
        result.textContent = data.message || '응답을 받지 못했습니다.';
      } catch (err) {
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
    birth_date = data.get('birth_date', '')
    birth_time = data.get('birth_time', '')
    gender = data.get('gender', '')

    parts = []
    if birth_date:
        parts.append(f"생년월일 {birth_date}")
    if birth_time:
        parts.append(f"태어난 시간 {birth_time}")
    if gender:
        parts.append(f"성별 {gender}")
    detail = ", ".join(parts) if parts else "입력 정보 없음"

    return jsonify({"message": f"사주 분석 준비 중: {detail}"})


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
