"""유료 서비스 출시 전 '읽기 전용' 점검 명령.

    python check_launch.py

하는 일:
- 사업자 표시 정보·문의처(BIZ_* 환경변수), 법적 고지(약관·개인정보·환불)의 미확정 문구,
  상품별 전달기한, '전문가 검토' 표현, 결제 모드·키 조합이 출시 가능한 상태인지 확인한다.
- 미완료 항목을 한국어로 짧게 묶어 출력한다.

원칙(중요):
- 읽기 전용이다. 환경변수·파일·DB·결제 설정을 **바꾸지 않는다**. 결제를 활성화하지 않는다.
- 실제 DB 에 접속하거나 자료를 삭제하지 않는다(개인정보 자동 삭제 기능을 만들지 않는다).
- 비밀값(키·시크릿)은 출력하지 않는다. 환경변수 '이름'과 통과/미완료 상태만 표시한다.
- 개인정보가 '자동 삭제 운영 중'인 것처럼 표시하지 않는다.

종료코드: 출시 차단 항목이 없으면 0, 하나라도 있으면 1(설정 변경은 하지 않는다).
"""

import os
import re

import legal_pages
import payments

PASS = "통과"
PENDING = "미완료"

# 상품 전달기한에서 '아직 확정 안 됨'을 뜻하는 자리표시 문구.
_ETA_PLACEHOLDER = "주문 시 안내"
# 법적 고지에서 '미확정'을 뜻하는 표식(legal_pages.PENDING 과 동일).
_LEGAL_PENDING = legal_pages.PENDING


def _env(name, env):
    return (env.get(name) or "").strip()


def _result(key, label, ok, detail=""):
    return {"key": key, "label": label, "status": PASS if ok else PENDING, "detail": detail}


def check_business_info(env):
    """사업자 표시 정보(상호 제외 필수 항목). 값은 출력하지 않고 환경변수 이름만 쓴다."""
    fields = [("BIZ_REPRESENTATIVE", "대표자"), ("BIZ_REG_NO", "사업자등록번호"),
              ("BIZ_MAIL_ORDER_NO", "통신판매업 신고번호(또는 해당없음 상태)"),
              ("BIZ_ADDRESS", "사업장 주소")]
    missing = [name for name, _ in fields if not _env(name, env)]
    detail = "" if not missing else "미설정 환경변수: " + ", ".join(missing)
    return _result("business_info", "사업자 표시 정보", not missing, detail)


def check_contact(env):
    """고객 문의처(전화·이메일)."""
    fields = [("BIZ_PHONE", "전화"), ("BIZ_EMAIL", "이메일")]
    missing = [name for name, _ in fields if not _env(name, env)]
    detail = "" if not missing else "미설정 환경변수: " + ", ".join(missing)
    return _result("contact", "고객 문의처", not missing, detail)


def _iter_strings(doc):
    for sec in doc.get("sections", []):
        for key in ("p", "li"):
            for s in sec.get(key, []):
                yield s
        if sec.get("h"):
            yield sec["h"]


def check_legal_pending():
    """약관·개인정보·환불 문서에 남은 미확정('입력 대기') 문구를 센다."""
    labels = {"terms": "이용약관", "privacy": "개인정보처리방침", "refund": "환불 안내"}
    pending_docs = []
    for key, label in labels.items():
        doc = legal_pages.document(key)
        n = sum(s.count(_LEGAL_PENDING) for s in _iter_strings(doc))
        if n:
            pending_docs.append("%s(%d곳)" % (label, n))
    detail = "" if not pending_docs else "미확정 문구: " + ", ".join(pending_docs)
    return _result("legal", "법적 고지 미확정 문구", not pending_docs, detail)


def _app_js_text(app_js=None):
    if app_js is not None:
        return app_js
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def check_product_eta(app_js=None):
    """상품별 전달기한이 자리표시('주문 시 안내')로 남아 있는지 확인한다."""
    text = _app_js_text(app_js)
    etas = re.findall(r'eta:\s*"([^"]*)"', text)
    unresolved = [e for e in etas if _ETA_PLACEHOLDER in e]
    detail = "" if not unresolved else "자리표시 전달기한 %d개(상품 안내 화면 '%s')" % (
        len(unresolved), _ETA_PLACEHOLDER)
    return _result("product_eta", "상품별 전달기한", not unresolved, detail)


def check_expert_claim(app_js=None):
    """'전문가' 표현이 쓰였는지 확인한다. 실제 유자격 전문가 검토인지 확정 전이면 미완료로 둔다."""
    text = _app_js_text(app_js)
    used = "전문가" in text
    # 표현이 있으면 '실제 전문가 검토 여부 확정 필요'를 미완료로 보고(없으면 통과).
    detail = "" if not used else "'전문가' 표현 사용 중 — 실제 유자격 전문가 검토 여부 확정 필요(아니면 표현/상품 수정)"
    return _result("expert_claim", "전문가 검토 표현", not used, detail)


def check_payment_config():
    """결제 모드·키 조합. 비밀값은 읽지도 출력하지도 않고 상태 플래그만 쓴다."""
    st = payments.config_status()  # enabled/mode/has_keys/keys_match_mode (값 비노출)
    ok = bool(st.get("enabled"))
    reasons = []
    if not st.get("has_keys"):
        reasons.append("키 미설정(TOSS_CLIENT_KEY/TOSS_SECRET_KEY/ORDER_ENCRYPTION_KEY)")
    if not st.get("keys_match_mode"):
        reasons.append("키 접두사가 PAYMENT_MODE 와 불일치(test_/live_)")
    if not ok and not reasons:
        reasons.append("PAYMENTS_ENABLED 미설정")
    detail = "" if ok else "; ".join(reasons)
    return _result("payment", "결제 모드·키 조합", ok, detail)


def run_checks(env=None, app_js=None):
    """모든 점검을 수행하고 결과 리스트를 돌려준다(부작용 없음)."""
    env = os.environ if env is None else env
    return [
        check_business_info(env),
        check_contact(env),
        check_legal_pending(),
        check_product_eta(app_js),
        check_expert_claim(app_js),
        check_payment_config(),
    ]


def format_report(results):
    lines = ["== 출시 전 점검 (읽기 전용) =="]
    for r in results:
        mark = "[O]" if r["status"] == PASS else "[ ]"
        line = "%s %s: %s" % (mark, r["label"], r["status"])
        if r["detail"]:
            line += " — " + r["detail"]
        lines.append(line)
    blockers = [r for r in results if r["status"] == PENDING]
    if blockers:
        lines.append("")
        lines.append("출시 차단 %d건: %s" % (len(blockers),
                                        ", ".join(r["label"] for r in blockers)))
        lines.append("비고: 개인정보는 자동으로 삭제되지 않으며, 요청 시 파기합니다(기간 경과 일괄 파기 기능 미도입).")
    else:
        lines.append("")
        lines.append("모든 항목 통과. (결제 활성화 여부는 이 명령이 바꾸지 않습니다.)")
    return "\n".join(lines)


def main():
    results = run_checks()
    print(format_report(results))
    return 0 if all(r["status"] == PASS for r in results) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
