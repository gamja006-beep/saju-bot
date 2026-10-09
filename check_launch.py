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

# 상품 전달기한에서 '아직 확정 안 됨'을 뜻하는 표식(자리표시 또는 '제안').
_ETA_UNRESOLVED = ("주문 시 안내", "제안")


def _result(key, label, ok, detail=""):
    return {"key": key, "label": label, "status": PASS if ok else PENDING, "detail": detail}


def _blank(info, key):
    return not str(info.get(key) or "").strip()


def check_business_info(info=None, env=None):
    """사업자 표시 정보. 상호·대표자·등록번호·통신판매업 신고번호는 확정값으로 통과한다.
    주소는 동·호수 없는 '초안'만 있으므로, 최종 공개 표기를 BIZ_ADDRESS 로 설정하기 전에는
    (화면 표시와 무관하게) 차단한다."""
    info = legal_pages.business_info() if info is None else info
    env = os.environ if env is None else env
    missing = []
    for key, name in [("representative", "BIZ_REPRESENTATIVE"), ("reg_no", "BIZ_REG_NO"),
                      ("mail_order_no", "BIZ_MAIL_ORDER_NO")]:
        if _blank(info, key):
            missing.append(name)
    if not (env.get("BIZ_ADDRESS") or "").strip():
        missing.append("BIZ_ADDRESS(주소 공개 표기 확정)")
    detail = "" if not missing else "미확정: " + ", ".join(missing)
    return _result("business_info", "사업자 표시 정보", not missing, detail)


def check_contact(info=None, env=None):
    """고객 문의처. 이메일은 확정값으로 통과하되, 문의 전화는 미정이므로 BIZ_PHONE 설정 전까지
    차단한다(비대면 이메일 운영 유지, 전화·화상 상담 기능은 추가하지 않음). 값은 출력하지 않는다."""
    info = legal_pages.business_info() if info is None else info
    env = os.environ if env is None else env
    missing = []
    if _blank(info, "email"):
        missing.append("BIZ_EMAIL")
    if not (env.get("BIZ_PHONE") or "").strip():
        missing.append("BIZ_PHONE")
    detail = "" if not missing else "미확정: " + ", ".join(missing)
    return _result("contact", "고객 문의처", not missing, detail)


def check_legal_pending():
    """약관·개인정보·환불 문서에 남은 미확정('입력 대기') 문구를 센다(legal_pages 와 동일 기준)."""
    pending_docs = legal_pages.documents_with_pending()
    detail = "" if not pending_docs else "미확정 문구: " + ", ".join(
        "%s(%d곳)" % (label, n) for label, n in pending_docs)
    return _result("legal", "법적 고지 미확정 문구", not pending_docs, detail)


def _app_js_text(app_js=None):
    if app_js is not None:
        return app_js
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "app.js")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def check_product_eta(app_js=None):
    """상품별 전달기한이 자리표시('주문 시 안내')나 '제안'으로 남아 있는지 확인한다.
    '제안'(운영 확인 전)도 확정 전이므로 미완료로 본다."""
    text = _app_js_text(app_js)
    etas = re.findall(r'eta:\s*"([^"]*)"', text)
    unresolved = [e for e in etas if any(m in e for m in _ETA_UNRESOLVED)]
    detail = "" if not unresolved else "미확정 전달기한 %d개(자리표시 또는 '제안')" % len(unresolved)
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


def run_checks(app_js=None, info=None, env=None):
    """모든 점검을 수행하고 결과 리스트를 돌려준다(부작용 없음)."""
    info = legal_pages.business_info() if info is None else info
    env = os.environ if env is None else env
    return [
        check_business_info(info, env),
        check_contact(info, env),
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
