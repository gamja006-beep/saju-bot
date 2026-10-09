"""운영자 알림(n8n 요청) 계층.

알파랩 운영자에게 신규 PAID 주문을 알리기 위해, 서버는 직접 이메일/텔레그램을
발송하지 않고 **n8n Webhook** 에 최소 정보만 담은 요청을 보낸다. 실제 이메일 수신
주소·텔레그램 Chat ID·사이트 주소는 n8n 쪽에서 관리한다.

원칙:
- 전송 payload 는 아래 4개 필드만 사용한다(고객 이름·이메일·출생정보·질문·명식·
  결제키·결제금액 미포함).
- 비밀값(헤더 시크릿)은 환경변수에서만 읽고 로그/응답/화면에 출력하지 않는다.
- HTTPS TLS 검증과 유한한 연결·응답 타임아웃을 적용한다.
- HTTP 접수(200)만으로 성공 처리하지 않는다. 응답의 event_id/order_id/channel 일치와
  status=="sent" 까지 확인해야 SENT 로 본다.
- 명확한 실패와 발송 여부 불명확(타임아웃·응답 유실)을 구분한다. 불명확은 자동
  재발송하지 않고 UNKNOWN 으로 표시한다.
- "정확히 1회 전달"·"수신함 도착"·"운영자 열람"을 보장하지 않는다(at-least-once + 멱등 키).

외부 호출은 drain 단계에서만 일어나며, 결제 확인(approve_payment) 경로 안에서는
실행하지 않는다(아웃박스에 기록만 함).
"""

import os
import ssl
import json
import socket
import secrets
import datetime
import urllib.request
import urllib.error

import payments

# n8n Header Auth 와 맞출 인증 헤더 이름(값은 N8N_NOTIFICATION_HEADER_SECRET).
# 서버 계약 고정: 헤더 이름은 반드시 X-AlphaLab-Notify-Token 이어야 n8n Header Auth 통과.
NOTIFY_HEADER_NAME = "X-AlphaLab-Notify-Token"
# 채널별 n8n 요청 상한 3초. 웹훅 즉시 전송 시 두 채널 순차 처리해도 6초(+Toss 조회 2초=8초).
NOTIFY_HTTP_TIMEOUT = 3
_CLAIM_LIMIT = 50


class NotifyAmbiguous(Exception):
    """발송 여부가 불명확한 결과(타임아웃·응답 유실). 자동 재발송 금지."""


def notify_config():
    """환경변수에서 알림 설정을 읽는다. 비밀값 자체는 반환하지 않고 유무만 노출한다."""
    enabled = (os.environ.get("OPERATOR_NOTIFICATIONS_ENABLED") or "").lower() == "true"
    url = os.environ.get("N8N_NOTIFICATION_WEBHOOK_URL") or ""
    secret = os.environ.get("N8N_NOTIFICATION_HEADER_SECRET") or ""
    return {
        "enabled": enabled,
        "has_url": bool(url),
        "has_secret": bool(secret),
        "ready": bool(enabled and url and secret),
        # 내부 사용(발송 시점에만 참조, 외부로 반환 금지)
        "_url": url,
        "_secret": secret,
    }


def notify_status_view():
    """관리자 화면용. 비밀값·URL 원문 없이 상태만."""
    cfg = notify_config()
    return {"enabled": cfg["enabled"], "has_url": cfg["has_url"],
            "has_secret": cfg["has_secret"], "ready": cfg["ready"]}


def build_payload(order_id, channel, event_id):
    """전송 본문(4개 필드 고정). PII·결제정보·비밀값 없음."""
    return {
        "event_type": "order.paid",
        "event_id": event_id,
        "order_id": order_id,
        "channel": channel,
    }


def _http_post(url, body, secret, timeout=NOTIFY_HTTP_TIMEOUT):
    """n8n Webhook 로 POST. 반환: (status_code, parsed_json 또는 None).

    - 서버가 응답한 non-2xx(HTTPError)는 (code, parsed) 로 반환(명확한 실패).
    - 타임아웃/응답 유실은 NotifyAmbiguous 로 올린다(불명확).
    - 연결 실패 등 기타 URLError 는 그대로 올린다(명확한 실패로 분류).
    """
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", NOTIFY_HEADER_NAME: secret},
    )
    ctx = ssl.create_default_context()  # 기본 컨텍스트 = 인증서/호스트명 검증 수행
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
            raw = resp.read()
            code = resp.getcode()
    except urllib.error.HTTPError as e:  # 서버가 상태코드로 응답 → 명확
        try:
            parsed = json.loads(e.read().decode("utf-8"))
        except Exception:
            parsed = None
        return e.code, parsed
    except socket.timeout:
        raise NotifyAmbiguous("timeout")
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise NotifyAmbiguous("timeout")
        raise  # 연결 거부/DNS 등 → 명확한 실패로 상위에서 분류
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        # 접수는 됐으나 본문을 읽지 못함 → 발송 성공 단정 불가(불명확)
        raise NotifyAmbiguous("unreadable_response")
    return code, parsed


def classify(order_id, channel, event_id, http_post):
    """한 채널 전송을 시도하고 결과를 분류한다.

    반환: ("sent"|"failed"|"unknown", error_type 또는 None).
    """
    body = build_payload(order_id, channel, event_id)
    try:
        code, parsed = http_post(body)
    except NotifyAmbiguous as e:
        # 타임아웃·응답 유실: 발송 여부 불명확 → 자동 재시도 금지.
        return "unknown", ("ambiguous_%s" % (str(e) or "unknown"))[:40]
    except Exception:
        # 서버에 도달하지 못한 전송 계층 실패(연결 거부/DNS/TLS)만 '명확한 미발송'으로
        # 보고 재시도한다. 이 경우에만 중복 발송 위험이 없다.
        return "failed", "transport_error"
    # 여기부터는 서버가 'HTTP 응답'을 돌려준 경우다. 확정 성공이 아니면 발송 여부를
    # 단정할 수 없으므로(워크플로가 이미 Gmail/Telegram 노드를 실행했을 수 있음)
    # 실패로 단정하지 않고 UNKNOWN 으로 둔다(자동 재발송 제외, 운영자 수동 확인).
    if code != 200:
        return "unknown", ("http_%d" % code)  # 5xx 포함: 미발송이라 단정하지 않음
    if not isinstance(parsed, dict):
        return "unknown", "unreadable_response"
    if (parsed.get("event_id") == event_id and parsed.get("order_id") == order_id
            and parsed.get("channel") == channel and parsed.get("status") == "sent"):
        return "sent", None
    return "unknown", "response_unconfirmed"


def _process(store, now, http_post, limit, order_id=None):
    """선점→전송→결과기록 공통 루프. order_id 가 있으면 그 주문만, 없으면 전체 대기열.

    - 설정이 비활성/미완료면 **외부 요청 없이** 그 상태만 반환한다.
    - PENDING/FAILED(재시도 시각 도래)만 선점한다. SENT/UNKNOWN 은 건너뛴다.
    - lease 로 동시 worker·관리자 요청·즉시 알림의 중복 실행을 막고, lease 만료분은 회수한다.
    """
    cfg = notify_config()
    if not cfg["ready"]:
        return {"status": "disabled", "enabled": cfg["enabled"],
                "has_url": cfg["has_url"], "has_secret": cfg["has_secret"],
                "claimed": 0, "sent": 0, "failed": 0, "unknown": 0}

    now = now or payments._now()
    lease_until = now + datetime.timedelta(seconds=payments.NOTIFY_LEASE_SECONDS)
    # 이 실행의 펜싱 토큰. 선점한 작업에 각인하고, 결과 기록 시 토큰이 일치할 때만
    # 반영한다 → lease 만료 후 뒤늦게 깨어난 이전 worker 가 새 worker 의 결과를 덮어쓰지 못한다.
    lease_token = secrets.token_hex(8)
    claimed = store.claim_pending_notifications(
        now, lease_until, lease_token, limit=limit, order_id=order_id)

    url, secret = cfg["_url"], cfg["_secret"]
    poster = http_post or (lambda body: _http_post(url, body, secret))
    counts = {"status": "ran", "claimed": len(claimed), "sent": 0, "failed": 0, "unknown": 0}
    for job in claimed:
        oid, ch, eid = job["order_id"], job["channel"], job["event_id"]
        attempts = int(job.get("attempts", 0)) + 1
        result, err = classify(oid, ch, eid, poster)
        if result == "sent":
            store.mark_notification_sent(oid, ch, now, lease_token)
            counts["sent"] += 1
        elif result == "failed":
            store.mark_notification_failed(
                oid, ch, now, attempts, payments.notification_next_retry(now, attempts),
                err, lease_token)
            counts["failed"] += 1
        else:  # unknown
            store.mark_notification_unknown(oid, ch, now, attempts, err, lease_token)
            counts["unknown"] += 1
    return counts


def drain(store, now=None, http_post=None, limit=_CLAIM_LIMIT):
    """전체 미발송 아웃박스를 선점해 n8n 에 전송한다(운영 진단·수동 복구용)."""
    return _process(store, now, http_post, limit, order_id=None)


def notify_order(store, order_id, now=None, http_post=None, limit=_CLAIM_LIMIT):
    """특정 주문의 PENDING/FAILED 알림만 즉시 전송한다(결제 commit 직후·관리자 수동 재전송).

    전역 대기열 전체가 아니라 현재 order_id 만 대상으로 한다. UNKNOWN/SENT 는 제외된다.
    """
    return _process(store, now, http_post, limit, order_id=order_id)


def notify_order_safe(store, order_id, now=None, http_post=None):
    """결제 성공 경로에서 호출하는 예외 안전 래퍼.

    알림 발송 실패·설정 오류·저장소 오류가 결제 성공 응답을 깨뜨리지 않도록 모든 예외를
    삼킨다(결제 PAID 와 고객 성공 화면은 알림 결과와 무관하게 유지). 비밀값은 다루지 않는다.
    반환: 집계 dict 또는 None(예외 억제 시).
    """
    try:
        return notify_order(store, order_id, now=now, http_post=http_post)
    except Exception:
        return None
