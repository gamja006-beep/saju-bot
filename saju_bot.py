import os
import unicodedata

from flask import Flask, request, jsonify, render_template

from saju_engine import compute_saju, SajuInputError

app = Flask(__name__)

# 유료 상품 식별자(서버측 검증용). 실제 결제는 연동하지 않는다(준비 중).
PRODUCT_IDS = {
    "free", "basic_9900", "deep_39000", "expert_99000",
    "life_290000", "relation_590000", "vip_990000",
}
CONSULTATION_TYPES = {"종합", "집중", "궁합"}

# 보고서용 자료에서 길이 제한(개인정보/제어문자 안전 처리).
_LIMITS = {
    "alias": 50,
    "question": 2000,
    "situation": 2000,
    "target_period": 100,
    "topic": 40,
}
_MAX_TOPICS = 20


def _clean_text(value, max_len):
    """제어문자 제거 + 길이 제한. 고객 입력을 안전한 평문으로 정규화한다."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    cleaned = []
    for ch in value:
        if ch in ("\n", "\t"):
            cleaned.append(ch)
            continue
        if unicodedata.category(ch)[0] == "C":  # 제어문자(C*) 제거
            continue
        cleaned.append(ch)
    return "".join(cleaned).strip()[:max_len]


def _extract_longitude(data):
    """선택 입력 birth_place에서 경도를 꺼낸다. (longitude 또는 None, error 또는 None)."""
    bp = data.get('birth_place')
    if bp is None:
        return None, None
    if not isinstance(bp, dict):
        return None, "birth_place는 객체여야 합니다."
    country = bp.get('country', 'KR')
    if country != 'KR':
        return None, "현재 대한민국(KR)만 지원합니다."
    return bp.get('longitude'), None


def build_report_data(data):
    """명리학 보고서 작성용 구조화 자료(dict)를 만든다.

    - 고객 입력은 instruction이 아니라 customer_data로 분리한다.
    - 이메일/전화/결제정보/상세주소는 포함하지 않는다(수집도 하지 않음).
    - 저장·로그·외부 호출 없음. 반환만 한다.
    """
    consultation_type = data.get('consultation_type', '종합')
    if consultation_type not in CONSULTATION_TYPES:
        raise SajuInputError("상담 유형은 종합/집중/궁합 중 하나여야 합니다.")

    selected_product = data.get('selected_product', 'free')
    if selected_product not in PRODUCT_IDS:
        raise SajuInputError("알 수 없는 상품입니다.")

    longitude, bp_error = _extract_longitude(data)
    if bp_error:
        raise SajuInputError(bp_error)

    saju = compute_saju(
        calendar=data.get('calendar', 'solar'),
        birth_date=data.get('birth_date'),
        birth_time=data.get('birth_time'),
        gender=data.get('gender'),
        is_leap_month=data.get('is_leap_month', False),
        longitude=longitude,
    )

    topics_in = data.get('topics', [])
    if not isinstance(topics_in, list):
        topics_in = [topics_in]
    topics = [_clean_text(t, _LIMITS["topic"]) for t in topics_in][:_MAX_TOPICS]
    topics = [t for t in topics if t]

    warnings = []
    if saju.get("needs_confirmation"):
        warnings.append("출생지(경도) 미입력: 진태양시 보정이 적용되지 않았습니다.")
    if saju["pillars"].get("time") is None:
        warnings.append("출생 시간 미상: 시주를 산출하지 않았습니다.")
    if saju.get("boundary_warning"):
        warnings.append("23시대 출생: 자시 규칙에 따라 일주/시주 해석이 달라질 수 있습니다.")

    report = {
        "schema": "saju_report_request_v1",
        "customer_data": {
            "alias": _clean_text(data.get('alias'), _LIMITS["alias"]),
            "consultation_type": consultation_type,
            "topics": topics,
            "question": _clean_text(data.get('question'), _LIMITS["question"]),
            "situation": _clean_text(data.get('situation'), _LIMITS["situation"]),
            "fortune_requested": bool(data.get('fortune_requested', False)),
            "target_period": _clean_text(data.get('target_period'), _LIMITS["target_period"]),
        },
        "saju": {
            "solar": saju["solar"],
            "lunar": saju["lunar"],
            "pillars": saju["pillars"],
            "sect": saju["sect"],
            "time_correction": saju["time_correction"],
            "boundary_warning": saju["boundary_warning"],
        },
        "verification_status": "PARTIAL / NOT_VERIFIED",
        "warnings": warnings,
        "selected_product": selected_product,
        "disclaimer": (
            "본 자료는 명리학 해석 참고용이며 미래를 확정적으로 단정하지 않습니다. "
            "한국 음력은 KASI 기준이나 전체 정확성은 독립 검증 전까지 확정되지 않았습니다."
        ),
    }
    return report


@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})


@app.route('/saju', methods=['POST'])
def saju():
    data = request.get_json(silent=True) or {}
    longitude, bp_error = _extract_longitude(data)
    if bp_error:
        return jsonify({"status": "error", "code": "invalid_input", "message": bp_error}), 400
    try:
        result = compute_saju(
            calendar=data.get('calendar', 'solar'),
            birth_date=data.get('birth_date'),
            birth_time=data.get('birth_time'),
            gender=data.get('gender'),
            is_leap_month=data.get('is_leap_month', False),
            longitude=longitude,
        )
    except SajuInputError as e:
        return jsonify({"status": "error", "code": "invalid_input", "message": str(e)}), 400
    except Exception:
        return jsonify({
            "status": "error",
            "code": "internal_error",
            "message": "사주 계산 중 오류가 발생했습니다.",
        }), 500
    return jsonify(result), 200


@app.route('/report-data', methods=['POST'])
def report_data():
    data = request.get_json(silent=True) or {}
    try:
        report = build_report_data(data)
    except SajuInputError as e:
        return jsonify({"status": "error", "code": "invalid_input", "message": str(e)}), 400
    except Exception:
        return jsonify({
            "status": "error",
            "code": "internal_error",
            "message": "보고서 자료 생성 중 오류가 발생했습니다.",
        }), 500
    return jsonify({"status": "ok", "report": report}), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
