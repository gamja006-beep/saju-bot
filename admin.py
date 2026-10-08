"""읽기 전용 관리자 주문 뷰어 (HTTP Basic 인증, HTTPS 전제).

운영자(알파랩)가 결제 완료(PAID) 주문만 확인한다.

보안 원칙:
- 읽기 전용(GET). 상태변경/삭제/환불/이메일 발송 없음.
- ADMIN_USERNAME/ADMIN_PASSWORD 미설정 시 관리자 경로 자체를 404로 숨긴다.
- 인증 비교는 secrets.compare_digest(타이밍 공격 완화).
- 이메일/상담자료는 서버에서 복호화해 한국어로만 표시. JSON/암호문/DB 내부값(payment_key,
  만료일, 암호화 토큰)은 노출하지 않는다.
- 비밀번호·암호화 키·DATABASE_URL 은 로그·HTML 에 출력하지 않는다.
- 무료 명식은 저장하지 않으므로 표시 대상이 아니다(주문 테이블에만 존재).
- 관리자 주소는 고객 화면에 링크하지 않으며 noindex 를 적용한다.

ORDER_STORE 는 앱 전역 저장소를 지연 참조한다(순환 import 방지).
"""

import os
import json
import secrets
from functools import wraps

from flask import Blueprint, request, Response, render_template, abort, make_response

import payments
import saju_insights
from saju_engine import compute_saju, SajuInputError

admin_bp = Blueprint("admin", __name__)

_NOINDEX = "noindex, nofollow, noarchive"
_MAX_LIST = 100

# 관리자 개인정보 화면 방어 헤더(관리자 블루프린트 응답에만 적용).
# 고객 페이지·결제·무료 명식 응답(app.route 들)은 블루프린트 범위 밖이라 영향 없음.
_SECURITY_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Robots-Tag": _NOINDEX,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@admin_bp.after_request
def _apply_security_headers(resp):
    """목록·상세·인증오류(401)·404 등 관리자 응답 전반에 캐시·참조·프레임 방어 적용."""
    for k, v in _SECURITY_HEADERS.items():
        resp.headers[k] = v
    return resp


def _get_store():
    import saju_bot  # 지연 import: saju_bot <-> admin 순환 방지
    return saju_bot.ORDER_STORE


def _admin_credentials():
    return os.environ.get("ADMIN_USERNAME") or "", os.environ.get("ADMIN_PASSWORD") or ""


def _admin_enabled():
    u, p = _admin_credentials()
    return bool(u and p)


def _check_auth(auth):
    if not auth:
        return False
    u, p = _admin_credentials()
    user_ok = secrets.compare_digest(auth.username or "", u)
    pass_ok = secrets.compare_digest(auth.password or "", p)
    return user_ok and pass_ok


def _unauthorized():
    return Response(
        "인증이 필요합니다.", 401,
        {
            "WWW-Authenticate": 'Basic realm="admin", charset="UTF-8"',
            "X-Robots-Tag": _NOINDEX,
        },
    )


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _admin_enabled():
            abort(404)  # 자격 미설정 시 경로 존재 자체를 숨김
        if not _check_auth(request.authorization):
            return _unauthorized()
        return fn(*args, **kwargs)
    return wrapper


def _with_noindex(html):
    resp = make_response(html)
    resp.headers["X-Robots-Tag"] = _NOINDEX
    return resp


def _mask_email(email):
    email = email or ""
    at = email.find("@")
    if at < 1:
        return "***"
    local, dom = email[:at], email[at:]
    return local[:min(2, len(local))] + "***" + dom


def _fmt_dt(dt):
    if dt is None:
        return "-"
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)[:16]


def _product_name(code):
    return payments.PRODUCTS.get(code, {}).get("name", code)


def _decrypt_email(store, order_id):
    """복호화 실패 시 빈 문자열(원문/예외 메시지 비노출)."""
    try:
        priv = store.get_private(order_id)
        if not priv:
            return ""
        return payments.decrypt(priv["encrypted_email"])
    except Exception:
        return ""


def _decrypt_payload(store, order_id):
    try:
        priv = store.get_private(order_id)
        if not priv:
            return {}
        data = json.loads(payments.decrypt(priv["encrypted_consultation_payload"]))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CAL_LABEL = {"solar": "양력", "lunar": "음력"}


def _humanize_consultation(d):
    """상담자료(dict)를 라벨-값의 한국어 목록으로. 값이 없으면 생략. (JSON/코드 미노출)"""
    rows = []

    def add(label, value):
        if value is None:
            return
        text = value if isinstance(value, str) else str(value)
        if text.strip():
            rows.append({"label": label, "value": text})

    ctype = d.get("consultation_type")
    add("상담 유형", ctype)

    cal = _CAL_LABEL.get(d.get("calendar"), d.get("calendar"))
    if d.get("is_leap_month"):
        cal = (cal or "") + " (윤달)"
    add("달력", cal)
    add("생년월일", d.get("birth_date"))
    add("태어난 시간", d.get("birth_time") or "미상")

    bp = d.get("birth_place")
    if isinstance(bp, dict):
        city = bp.get("city")
        add("출생 지역", city if (city and str(city).strip()) else "모름(미보정)")

    topics = d.get("topics")
    if isinstance(topics, list) and topics:
        add("관심 주제", ", ".join([str(t) for t in topics if str(t).strip()]))

    add("구체적인 질문", d.get("question"))
    add("현재 상황", d.get("situation"))
    add("대상 기간", d.get("target_period"))
    return rows


# ---- 명리학 전문 보고서 작성 자료 (서버 재계산 평문) ----
# 원칙: 브라우저가 보낸 명식 결과를 신뢰하지 않고, 저장된 정규화 입력으로 기존
# saju_engine 을 호출해 서버에서 계산한다. 이메일/주문번호/금액/결제키/암호문/경도
# 숫자/내부 JSON 은 절대 포함하지 않는다. 없는 값은 추정하지 않고 '미입력'으로 둔다.
_NA = "미입력"
_TIME_STATUS_LABEL = {"exact": "정확", "approx": "대략", "unknown": "미상"}
_ELEMENT_ORDER = ["목", "화", "토", "금", "수"]


def _txt(v):
    if v is None:
        return ""
    return (v if isinstance(v, str) else str(v)).strip()


def _or_na(v):
    return _txt(v) or _NA


def _gender_label(payload, saju):
    if saju:
        g = saju.get("input", {}).get("gender")
        if g in ("남", "여"):
            return "남성" if g == "남" else "여성"
    raw = _txt(payload.get("gender")).lower()
    return {"남": "남성", "여": "여성", "male": "남성", "female": "여성",
            "m": "남성", "f": "여성"}.get(raw or _txt(payload.get("gender")), _NA)


def _time_status_label(payload):
    st = _txt(payload.get("birth_time_status"))
    if st in _TIME_STATUS_LABEL:
        return _TIME_STATUS_LABEL[st]
    if _txt(payload.get("birth_time")):
        return "미입력(정확도 기록 없음)"
    return "미상"


def _pillar_text(p):
    """명식 한 기둥을 '甲子 (갑자)' 형태로. 한자와 한글 병기."""
    if not p:
        return None
    gz = _txt(p.get("ganzhi"))
    ko = _txt(p.get("gan_ko")) + _txt(p.get("zhi_ko"))
    if not gz:
        return None
    return "%s (%s)" % (gz, ko) if ko else gz


def _compute_from_payload(payload):
    """저장된 정규화 출생 입력으로 서버에서 명식을 계산한다.

    반환: (saju_dict 또는 None, 안전한 오류 안내문 또는 None).
    실패 시 내부 예외/비밀값을 노출하지 않고 일반 안내문만 돌려준다.
    """
    bp = payload.get("birth_place")
    longitude = None
    if isinstance(bp, dict) and (bp.get("country") or "KR") == "KR":
        longitude = bp.get("longitude")
    try:
        saju = compute_saju(
            calendar=payload.get("calendar", "solar"),
            birth_date=payload.get("birth_date"),
            birth_time=payload.get("birth_time"),
            gender=payload.get("gender"),
            is_leap_month=bool(payload.get("is_leap_month", False)),
            longitude=longitude,
        )
        return saju, None
    except SajuInputError:
        return None, "저장된 출생정보가 부족하거나 형식이 올바르지 않아 명식을 계산할 수 없습니다."
    except Exception:
        return None, "명식 계산 중 문제가 발생했습니다. 고객 출생정보를 다시 확인해 주세요."


def _build_report_package(order, payload):
    """전문 챗봇에 붙여넣을 수 있는 읽기 쉬운 한국어 보고서 작성 자료(평문)."""
    saju, saju_error = _compute_from_payload(payload)
    insight = None
    if saju is not None:
        try:
            topics = payload.get("topics")
            insight = saju_insights.build_free_result(
                saju, alias=payload.get("alias"),
                topics=topics if isinstance(topics, list) else [])
        except Exception:
            insight = None

    lines = []
    A = lines.append
    A("[명리학 전문 보고서 작성 자료]")
    A("")

    # 1. 상담 기본정보
    A("1. 상담 기본정보")
    A("- 상담 유형: " + _or_na(payload.get("consultation_type")))
    A("- 상담 대상 또는 별칭: " + _or_na(payload.get("alias")))
    A("- 성별: " + _gender_label(payload, saju))
    topics = payload.get("topics")
    topics_txt = ", ".join([_txt(t) for t in topics if _txt(t)]) if isinstance(topics, list) else ""
    A("- 관심 주제: " + (topics_txt or _NA))
    A("- 고객 질문: " + _or_na(payload.get("question")))
    A("- 현재 상황: " + _or_na(payload.get("situation")))
    A("- 살펴볼 기간: " + _or_na(payload.get("target_period")))
    A("")

    # 2. 출생 입력정보
    A("2. 출생 입력정보")
    A("- 달력: " + (_CAL_LABEL.get(payload.get("calendar")) or _NA))
    A("- 입력 생년월일: " + _or_na(payload.get("birth_date")))
    A("- 출생시간 상태: " + _time_status_label(payload))
    A("- 입력 출생시간: " + _or_na(payload.get("birth_time")))
    bp = payload.get("birth_place")
    country = _txt(bp.get("country")) if isinstance(bp, dict) else ""
    city = _txt(bp.get("city")) if isinstance(bp, dict) else ""
    A("- 출생 국가: " + (("대한민국(KR)" if country == "KR" else country) or _NA))
    A("- 출생 도시: " + (city or _NA))  # 경도 숫자는 보고서에 넣지 않는다
    A("")

    # 3. 계산 기준
    A("3. 계산 기준")
    if saju is None:
        A("- " + saju_error)
    else:
        solar = saju.get("solar", {})
        A("- 양력 환산일: %04d-%02d-%02d" % (
            solar.get("year", 0), solar.get("month", 0), solar.get("day", 0)))
        A("- 음력 환산일: " + _or_na(saju.get("lunar", {}).get("text")))
        A("- 윤달 여부: " + ("예" if saju.get("lunar", {}).get("is_leap_month") else "아니오"))
        tc = saju.get("time_correction", {})
        conv = saju.get("convention", {})
        if tc.get("applied"):
            applied_time = _txt(tc.get("true_solar_local")) + " (진태양시 보정 적용)"
        elif _txt(solar.get("time")):
            applied_time = _txt(solar.get("time")) + " (벽시계 기준, 보정 없음)"
        else:
            applied_time = "미상 (출생시간 미입력)"
        A("- 적용 시간: " + applied_time)
        tz = conv.get("timezone")
        A("- 시간대: " + ("대한민국 표준시(Asia/Seoul)" if tz == "Asia/Seoul"
                       else "한국 표준시 벽시계(KST, 보정 없음)"))
        A("- 진태양시 보정: " + ("적용" if conv.get("longitude_correction") else "미적용"))
        A("- 과거 표준시/DST 적용: " + (
            "적용" if (conv.get("historical_std_time") or conv.get("dst")) else "미적용"))
        A("- 자시 기준: 자정(00:00) 전환 (sect=2)")
        A("- 계산·해석 제한사항: " + _or_na(saju.get("accuracy_note")))
    A("")

    # 4. 사주 명식
    A("4. 사주 명식")
    if saju is None:
        A("- " + saju_error)
    else:
        pillars = saju.get("pillars", {})
        A("- 연주: " + (_pillar_text(pillars.get("year")) or _NA))
        A("- 월주: " + (_pillar_text(pillars.get("month")) or _NA))
        A("- 일주: " + (_pillar_text(pillars.get("day")) or _NA))
        tp = _pillar_text(pillars.get("time"))
        A("- 시주: " + (tp if tp else "출생시간 미상으로 시주 제외"))
    A("")

    # 5. 기초 분석자료
    A("5. 기초 분석자료")
    if saju is None or insight is None:
        A("- " + (saju_error or "기초 분석자료를 생성할 수 없습니다."))
    else:
        counts = insight.get("elements", {})
        A("- 오행 분포: " + " · ".join("%s %d" % (k, counts.get(k, 0)) for k in _ELEMENT_ORDER))
        dm = insight.get("day_master", {})
        dm_parts = []
        if dm.get("gan_ko") or dm.get("gan"):
            dm_parts.append("%s(%s)" % (dm.get("gan_ko", ""), dm.get("gan", "")))
        if dm.get("element"):
            dm_parts.append(dm["element"] + " 기운")
        if dm.get("polarity"):
            dm_parts.append(dm["polarity"])
        A("- 일간: " + (" / ".join(dm_parts) if dm_parts else _NA))
        A("- 주요 성향 키워드: " + (" · ".join(insight.get("keywords", [])) or _NA))
        A("- 강점: " + (" · ".join(insight.get("strengths", [])) or _NA))
        A("- 주의할 점: " + _or_na(insight.get("caution")))
        notes = insight.get("notes", [])
        A("- 시간 미상 또는 경계시간 경고: " + ("; ".join(notes) if notes else "특이 경고 없음"))
    A("")

    # 6. 보고서 요청사항
    A("6. 보고서 요청사항")
    A("- 선택 상품: " + _product_name(order.get("product_code")))
    A("- 보고서 범위: 명식 근거 → 명리적 해석 → 현실 적용 → 실행 제안 순서로 작성")
    A("- 확정적 예언, 공포 조장, 질병·투자·법률 단정 금지")
    A("- 출생시간 미상 항목은 추정하지 말고 한계를 명시")

    return "\n".join(lines)


@admin_bp.route("/admin/orders", methods=["GET"])
@require_admin
def admin_orders():
    store = _get_store()
    try:
        orders = store.list_paid_orders(limit=_MAX_LIST)
    except Exception:
        abort(500)  # 내부 정보 비노출
    rows = []
    for o in orders[:_MAX_LIST]:
        if o.get("status") != "PAID":  # 방어적 재확인
            continue
        rows.append({
            "order_id": o.get("order_id"),
            "product_name": _product_name(o.get("product_code")),
            "amount": o.get("amount"),
            "currency": o.get("currency", "KRW"),
            "paid_at": _fmt_dt(o.get("paid_at")),
            "email_masked": _mask_email(_decrypt_email(store, o.get("order_id"))),
        })
    return _with_noindex(render_template("admin_orders.html", orders=rows, count=len(rows)))


@admin_bp.route("/admin/orders/<order_id>", methods=["GET"])
@require_admin
def admin_order_detail(order_id):
    store = _get_store()
    try:
        order = store.get_order(order_id)
    except Exception:
        abort(500)
    if not order or order.get("status") != "PAID":
        abort(404)  # PAID 아닌/존재하지 않는 주문 접근 차단
    payload = _decrypt_payload(store, order_id)  # 한 번만 복호화
    view = {
        "order_id": order.get("order_id"),
        "product_name": _product_name(order.get("product_code")),
        "amount": order.get("amount"),
        "currency": order.get("currency", "KRW"),
        "paid_at": _fmt_dt(order.get("paid_at")),
        "email": _decrypt_email(store, order_id),
        "consultation": _humanize_consultation(payload),
        "report_text": _build_report_package(order, payload),
    }
    return _with_noindex(render_template("admin_order_detail.html", o=view))
