"""운영자 알림 아웃박스 drain 러너 (한 번 실행하고 종료; Railway 주기 실행용).

사용 예(Railway Scheduled Job 등 외부 스케줄러가 호출):
    python drain_notifications.py

동작 원칙:
- 설정을 환경변수에서만 읽는다. 기본 비활성(OPERATOR_NOTIFICATIONS_ENABLED!=true)이거나
  Webhook URL/시크릿이 없으면 **외부 호출 없이** 상태만 출력하고 종료한다(저장소 연결도 안 함).
- 한 번에 처리할 작업 수(batch)와 전체 실행 시간(deadline)을 제한한다.
- 중복 실행에도 저장소의 lease·펜싱 토큰이 중복 처리를 막는다(여기서 추가 잠금은 만들지 않음).
- 비밀키·전체 Webhook URL·고객 개인정보를 출력하지 않는다(건수와 상태만).
- 웹서버를 띄우거나 스키마 변경(init)을 실행하지 않는다. 저장소 생성만 한다.

종료코드: 정상 0, 예기치 못한 오류 1.
"""

import os
import sys
import time

import payments
import notifier


def _int_env(name, default):
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def run(now=None, time_fn=time.monotonic):
    """아웃박스를 배치로 drain 한다. 반환: 안전한 집계 dict(비밀값·PII 없음)."""
    cfg = notifier.notify_config()
    if not cfg["ready"]:
        # 비활성/미완료: 외부 호출·DB 연결 없이 상태만.
        return {"status": "disabled", "enabled": cfg["enabled"],
                "has_url": cfg["has_url"], "has_secret": cfg["has_secret"],
                "claimed": 0, "sent": 0, "failed": 0, "unknown": 0, "batches": 0}

    batch = _int_env("NOTIFY_DRAIN_BATCH", 50)
    max_batches = _int_env("NOTIFY_DRAIN_MAX_BATCHES", 20)
    max_seconds = _int_env("NOTIFY_DRAIN_MAX_SECONDS", 25)

    store = payments.make_default_store()  # DATABASE_URL 있으면 Postgres (스키마 생성 안 함)
    totals = {"status": "ran", "claimed": 0, "sent": 0, "failed": 0, "unknown": 0, "batches": 0}
    start = time_fn()
    for _ in range(max_batches):
        if time_fn() - start >= max_seconds:
            totals["status"] = "time_limit"
            break
        summary = notifier.drain(store, now=now, limit=batch)
        totals["batches"] += 1
        for k in ("claimed", "sent", "failed", "unknown"):
            totals[k] += summary.get(k, 0)
        if summary.get("claimed", 0) == 0:
            break  # 처리할 작업 없음
    return totals


def main():
    try:
        summary = run()
    except Exception as e:
        # 예외 '종류'만 출력(메시지에 DSN/시크릿/URL 이 섞이지 않도록).
        sys.stderr.write("drain failed: %s\n" % type(e).__name__)
        return 1
    # 비밀값·URL·PII 없이 집계만 출력.
    print("drain %s: batches=%d claimed=%d sent=%d failed=%d unknown=%d" % (
        summary["status"], summary["batches"], summary["claimed"],
        summary["sent"], summary["failed"], summary["unknown"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
