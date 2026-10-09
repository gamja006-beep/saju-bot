"""출시 전 점검 명령(check_launch.py) 합성 테스트.

실 DB·실 네트워크·실 결제 없이 환경변수와 합성 문자열로만 검증한다.
비밀값을 단언에 노출하지 않으며, 점검 명령이 설정을 바꾸지 않음을 확인한다.
"""

import io
import os
import sys
import copy
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import check_launch
import legal_pages
import payments
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode("ascii")
_ALL_ENV = ["BIZ_NAME", "BIZ_REPRESENTATIVE", "BIZ_REG_NO", "BIZ_MAIL_ORDER_NO",
            "BIZ_ADDRESS", "BIZ_PHONE", "BIZ_EMAIL",
            "PAYMENTS_ENABLED", "PAYMENT_MODE", "TOSS_CLIENT_KEY", "TOSS_SECRET_KEY",
            "ORDER_ENCRYPTION_KEY"]

_CLEAN_JS = 'var PRODUCTS = [{ eta: "즉시" }, { eta: "영업일 기준(3일)" }]; // 담당자 확인'
_PLACEHOLDER_JS = 'var PRODUCTS = [{ eta: "영업일 기준(주문 시 안내)" }];'
_EXPERT_JS = 'name: "전문가 보고서"'


def _fill_business(env):
    env["BIZ_REPRESENTATIVE"] = "홍길동"
    env["BIZ_REG_NO"] = "000-00-00000"
    env["BIZ_MAIL_ORDER_NO"] = "해당없음(간이과세자)"
    env["BIZ_ADDRESS"] = "서울시 어딘가 1-2"
    env["BIZ_PHONE"] = "02-000-0000"
    env["BIZ_EMAIL"] = "help@example.test"


def _enable_payments(env):
    env["PAYMENTS_ENABLED"] = "true"
    env["PAYMENT_MODE"] = "test"
    env["TOSS_CLIENT_KEY"] = "test_ck_sampleclient"
    env["TOSS_SECRET_KEY"] = "test_sk_SUPERSECRET_value_123"
    env["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ALL_ENV}
        for k in _ALL_ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class StatusHelpersTest(unittest.TestCase):
    def test_product_eta_placeholder_is_pending(self):
        self.assertEqual(check_launch.check_product_eta(_PLACEHOLDER_JS)["status"], check_launch.PENDING)

    def test_product_eta_resolved_is_pass(self):
        self.assertEqual(check_launch.check_product_eta(_CLEAN_JS)["status"], check_launch.PASS)

    def test_product_eta_proposal_is_pending(self):
        # '제안'(운영 확인 전)도 확정 전이므로 차단 대상이다.
        js = 'x eta: "3영업일(제안, 운영 확인 전)"'
        self.assertEqual(check_launch.check_product_eta(js)["status"], check_launch.PENDING)

    def test_expert_claim_present_is_pending(self):
        self.assertEqual(check_launch.check_expert_claim(_EXPERT_JS)["status"], check_launch.PENDING)

    def test_expert_claim_absent_is_pass(self):
        self.assertEqual(check_launch.check_expert_claim(_CLEAN_JS)["status"], check_launch.PASS)

    def test_business_info_missing_is_pending(self):
        r = check_launch.check_business_info({}, {})  # 정보/환경변수 모두 비면 전부 미확정
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("BIZ_ADDRESS", r["detail"])

    def test_business_info_complete_is_pass(self):
        info = {"representative": "오준영", "reg_no": "564-05-02583",
                "mail_order_no": "2025-고양덕양구-2991"}
        env = {"BIZ_ADDRESS": "경기도 고양시 덕양구 중앙로558번길 57 101동 101호"}
        self.assertEqual(check_launch.check_business_info(info, env)["status"], check_launch.PASS)

    def test_business_info_confirmed_defaults_pass_but_address_blocks(self):
        # 신고번호·대표자·등록번호는 확정 기본값으로 통과하되, 주소 공개표기(BIZ_ADDRESS) 미설정이면 차단.
        import legal_pages as lp
        info = lp.business_info()  # 주소는 초안 기본값이 보이지만 BIZ_ADDRESS 환경변수는 비어 있음
        self.assertEqual(info["representative"], "오준영")
        self.assertEqual(info["reg_no"], "564-05-02583")
        r = check_launch.check_business_info(info, {})
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("BIZ_ADDRESS", r["detail"])

    def test_contact_missing_phone_is_pending_even_with_email_default(self):
        import legal_pages as lp
        info = lp.business_info()  # email 은 기본값 존재, phone 은 비어 있음
        r = check_launch.check_contact(info, {})
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("BIZ_PHONE", r["detail"])


class RealArtifactsTest(_EnvCase):
    def test_real_app_js_still_has_placeholder_and_expert(self):
        # 현재 상품 문구는 전달기한 자리표시와 '전문가' 표현을 아직 포함한다(출시 전 확정 대상).
        self.assertEqual(check_launch.check_product_eta()["status"], check_launch.PENDING)
        self.assertEqual(check_launch.check_expert_claim()["status"], check_launch.PENDING)

    def test_real_legal_pages_have_pending_markers(self):
        self.assertEqual(check_launch.check_legal_pending()["status"], check_launch.PENDING)


class DevEnvBlockersTest(_EnvCase):
    def test_empty_env_reports_blockers_and_exit_1(self):
        results = check_launch.run_checks()
        by = {r["key"]: r["status"] for r in results}
        for key in ("business_info", "contact", "payment"):
            self.assertEqual(by[key], check_launch.PENDING, key)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = check_launch.main()
        self.assertEqual(rc, 1)
        self.assertIn("출시 차단", buf.getvalue())


class AllPassTest(_EnvCase):
    def test_fully_configured_synthetic_all_pass(self):
        _fill_business(os.environ)
        _enable_payments(os.environ)
        clean_doc = {"sections": [{"h": "x", "p": ["확정된 문구"], "li": []}]}
        with mock.patch.object(legal_pages, "document", return_value=clean_doc):
            results = check_launch.run_checks(app_js=_CLEAN_JS)
        self.assertTrue(all(r["status"] == check_launch.PASS for r in results),
                        [r for r in results if r["status"] != check_launch.PASS])


class SafetyTest(_EnvCase):
    def test_does_not_print_secret_values(self):
        _enable_payments(os.environ)
        buf = io.StringIO()
        with redirect_stdout(buf):
            check_launch.main()
        out = buf.getvalue()
        # 비밀값 원문은 출력되지 않아야 한다(환경변수 '이름'만 허용).
        self.assertNotIn("test_sk_SUPERSECRET_value_123", out)
        self.assertNotIn(_FERNET_KEY, out)
        self.assertNotIn("test_ck_sampleclient", out)

    def test_read_only_does_not_change_env_or_enable_payments(self):
        before = copy.deepcopy(dict(os.environ))
        self.assertFalse(payments.payments_enabled())  # 시작 시 비활성
        check_launch.run_checks()
        buf = io.StringIO()
        with redirect_stdout(buf):
            check_launch.main()
        self.assertEqual(dict(os.environ), before)      # 환경변수 불변
        self.assertFalse(payments.payments_enabled())   # 결제 여전히 비활성

    def test_no_auto_deletion_claim_in_output(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            check_launch.main()
        out = buf.getvalue()
        # 자동 삭제가 '운영 중'인 것처럼 단정하는 표현이 없어야 한다(부정 안내는 허용).
        for bad in ("자동으로 삭제합니다", "자동 삭제됩니다", "자동으로 파기합니다", "90일 후 삭제"):
            self.assertNotIn(bad, out)
        self.assertIn("자동으로 삭제되지 않", out)  # 미가동 사실을 명시


class LivePaymentGuardTest(_EnvCase):
    def _set_live_keys(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "live"
        os.environ["TOSS_CLIENT_KEY"] = "live_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "live_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY

    def test_launch_blockers_lists_address_and_phone_when_unset(self):
        joined = " ".join(payments.launch_blockers())
        self.assertIn("BIZ_ADDRESS", joined)
        self.assertIn("BIZ_PHONE", joined)

    def test_launch_blockers_empty_when_both_set(self):
        os.environ["BIZ_ADDRESS"] = "경기도 고양시 덕양구 중앙로558번길 57 101동 101호"
        os.environ["BIZ_PHONE"] = "031-000-0000"
        self.assertEqual(payments.launch_blockers(), [])

    def test_live_payments_blocked_until_address_and_phone_set(self):
        self._set_live_keys()
        self.assertTrue(payments.payments_enabled())      # 설정상으로는 enabled
        self.assertFalse(payments.live_payments_ready())  # 주소·전화 미확정 → live 미준비
        os.environ["BIZ_ADDRESS"] = "경기도 고양시 덕양구 중앙로558번길 57 101동 101호"
        os.environ["BIZ_PHONE"] = "031-000-0000"
        self.assertTrue(payments.live_payments_ready())

    def test_test_mode_not_blocked_by_guard(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        self.assertTrue(payments.live_payments_ready())  # test 모드는 가드 영향 없음

    def test_api_orders_blocks_live_without_address_and_phone(self):
        import saju_bot
        self._set_live_keys()  # 주소·전화 미설정
        c = saju_bot.app.test_client()
        r = c.post("/api/orders", json={"product_code": "BASIC", "email": "x@example.test"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json().get("code"), "launch_incomplete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
