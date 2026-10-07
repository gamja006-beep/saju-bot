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
    view = {
        "order_id": order.get("order_id"),
        "product_name": _product_name(order.get("product_code")),
        "amount": order.get("amount"),
        "currency": order.get("currency", "KRW"),
        "paid_at": _fmt_dt(order.get("paid_at")),
        "email": _decrypt_email(store, order_id),
        "consultation": _humanize_consultation(_decrypt_payload(store, order_id)),
    }
    return _with_noindex(render_template("admin_order_detail.html", o=view))
