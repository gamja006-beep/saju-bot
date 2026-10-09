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
            "ORDER_ENCRYPTION_KEY",
            "PRODUCT_ETA_DAYS_BASIC", "PRODUCT_ETA_DAYS_DEEP", "PRODUCT_ETA_READY_BASIC",
            "PRODUCT_LIVE_READY_EXPERT", "PRODUCT_LIVE_READY_LIFE_DESIGN",
            "PRODUCT_LIVE_READY_RELATION_BUSINESS", "PRODUCT_LIVE_READY_ANNUAL_VIP"]



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


class StatusHelpersTest(_EnvCase):
    def test_launch_products_eta_pending_without_days(self):
        r = check_launch.check_launch_products()  # PRODUCT_ETA_DAYS_* 미설정
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("PRODUCT_ETA_DAYS_BASIC", r["detail"])
        self.assertIn("PRODUCT_ETA_DAYS_DEEP", r["detail"])

    def test_launch_products_eta_pass_when_both_days_set(self):
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        os.environ["PRODUCT_ETA_DAYS_DEEP"] = "5"
        self.assertEqual(check_launch.check_launch_products()["status"], check_launch.PASS)

    def test_launch_products_eta_pending_if_one_missing(self):
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"  # DEEP 미확정
        r = check_launch.check_launch_products()
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("PRODUCT_ETA_DAYS_DEEP", r["detail"])

    def test_eta_ready_boolean_flag_alone_does_not_confirm(self):
        # 보고된 허점: 불리언 플래그만으로는 확정되지 않는다(실제 일수 값이 있어야 함).
        os.environ["PRODUCT_ETA_READY_BASIC"] = "true"  # 레거시 플래그만
        r = check_launch.check_launch_products()
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("PRODUCT_ETA_DAYS_BASIC", r["detail"])

    def test_held_products_reported_as_hold_not_blocker(self):
        r = check_launch.check_held_products()
        self.assertEqual(r["status"], check_launch.HOLD)
        self.assertIn("EXPERT", r["detail"])

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
    def test_launch_products_blocked_until_eta_confirmed(self):
        # 환경변수 미설정(_EnvCase) → BASIC·DEEP 전달기한 미확정으로 차단된다.
        self.assertEqual(check_launch.check_launch_products()["status"], check_launch.PENDING)

    def test_held_products_are_hold_not_launch_blocker(self):
        self.assertEqual(check_launch.check_held_products()["status"], check_launch.HOLD)

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
    def test_fully_configured_synthetic_has_no_blockers(self):
        _fill_business(os.environ)
        _enable_payments(os.environ)
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        os.environ["PRODUCT_ETA_DAYS_DEEP"] = "5"
        clean_doc = {"sections": [{"h": "x", "p": ["확정된 문구"], "li": []}]}
        with mock.patch.object(legal_pages, "document", return_value=clean_doc):
            results = check_launch.run_checks()
        # 출시 차단(PENDING)이 하나도 없어야 한다. 보류 상품(HOLD)은 의도적 상태로 허용.
        pending = [r for r in results if r["status"] == check_launch.PENDING]
        self.assertEqual(pending, [], pending)
        self.assertTrue(any(r["status"] == check_launch.HOLD for r in results))


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

    def _set_biz(self):
        os.environ["BIZ_ADDRESS"] = "경기도 고양시 덕양구 중앙로558번길 57 101동 101호"
        os.environ["BIZ_PHONE"] = "031-000-0000"

    def test_launch_blockers_lists_all_common_conditions_when_unset(self):
        joined = " ".join(payments.launch_blockers())
        self.assertIn("BIZ_ADDRESS", joined)
        self.assertIn("BIZ_PHONE", joined)
        self.assertIn("법적 고지", joined)  # 약관·개인정보·환불 미확정도 공통 차단

    def test_launch_blockers_empty_only_when_biz_and_legal_resolved(self):
        self._set_biz()
        self.assertIn("법적 고지", " ".join(payments.launch_blockers()))  # 법적 고지 미확정이면 여전히 차단
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            self.assertEqual(payments.launch_blockers(), [])

    def test_live_blocked_by_each_condition(self):
        self._set_live_keys()
        self.assertTrue(payments.payments_enabled())
        # (1) 주소·전화·법적 모두 미확정
        self.assertFalse(payments.live_payments_ready())
        # (2) 주소·전화만 설정, 법적 고지 미확정 → 여전히 차단
        self._set_biz()
        self.assertFalse(payments.live_payments_ready())
        # (3) 법적 고지까지 해소 → 공통 조건 충족
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            self.assertTrue(payments.live_payments_ready())

    def test_test_mode_not_blocked_by_guard(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        self.assertTrue(payments.live_payments_ready())  # test 모드는 공통 가드 영향 없음

    def test_product_live_blocked_for_hold_and_unconfirmed_launch(self):
        for code in ("EXPERT", "LIFE_DESIGN", "RELATION_BUSINESS", "ANNUAL_VIP"):
            self.assertIsNotNone(payments.product_live_blocked(code), code)  # 보류
        for code in ("BASIC", "DEEP"):
            self.assertIsNotNone(payments.product_live_blocked(code), code)  # 전달기한 미확정
        for code in ("", "NOPE"):
            self.assertIsNone(payments.product_live_blocked(code), code)     # 알 수 없는 코드

    def test_hold_product_unblocked_by_live_ready_flag(self):
        self.addCleanup(lambda: os.environ.pop("PRODUCT_LIVE_READY_EXPERT", None))
        self.assertIsNotNone(payments.product_live_blocked("EXPERT"))
        os.environ["PRODUCT_LIVE_READY_EXPERT"] = "true"
        self.assertIsNone(payments.product_live_blocked("EXPERT"))

    def test_launch_product_unblocked_by_eta_days(self):
        self.assertIsNotNone(payments.product_live_blocked("BASIC"))  # 전달기한 미확정
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        self.assertIsNone(payments.product_live_blocked("BASIC"))

    def test_eta_ready_flag_alone_does_not_open_live(self):
        # 보고된 허점 재현·차단: 불리언 플래그만으로는 live 가 열리지 않는다.
        os.environ["PRODUCT_ETA_READY_BASIC"] = "true"
        self.assertIsNotNone(payments.product_live_blocked("BASIC"))
        self.assertIsNone(payments.product_delivery_eta("BASIC"))  # 확정 일수 없음

    def test_api_orders_common_block_live_without_biz(self):
        import saju_bot
        self._set_live_keys()  # 주소·전화 미설정 + 법적 고지 미확정
        c = saju_bot.app.test_client()
        r = c.post("/api/orders", json={"product_code": "BASIC", "email": "x@example.test"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json().get("code"), "launch_incomplete")

    def test_api_orders_product_block_live_expert_even_when_common_ok(self):
        import saju_bot
        self._set_live_keys()
        self._set_biz()
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        c = saju_bot.app.test_client()
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            r = c.post("/api/orders", json={"product_code": "EXPERT", "email": "x@example.test"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json().get("code"), "product_unavailable")

    def test_api_orders_basic_blocked_live_until_eta_confirmed(self):
        import saju_bot
        self._set_live_keys()
        self._set_biz()
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        c = saju_bot.app.test_client()
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            r = c.post("/api/orders", json={"product_code": "BASIC", "email": "x@example.test"})
        self.assertEqual(r.status_code, 503)  # 전달기한 미확정 → 차단
        self.assertEqual(r.get_json().get("code"), "product_unavailable")

    def test_api_orders_basic_allowed_live_when_eta_confirmed(self):
        import saju_bot
        self._set_live_keys()
        self._set_biz()
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        c = saju_bot.app.test_client()
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            r = c.post("/api/orders", json={"product_code": "BASIC", "email": "x@example.test"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("status"), "ok")

    def test_api_orders_test_mode_allows_hold_product(self):
        # test 모드는 상품별 가드를 적용하지 않는다(합성 테스트 흐름 유지).
        import saju_bot
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        c = saju_bot.app.test_client()
        r = c.post("/api/orders", json={"product_code": "EXPERT", "email": "x@example.test"})
        self.assertEqual(r.status_code, 200)

    def test_client_config_browser_disabled_live_until_ready(self):
        self._set_live_keys()  # 공통 조건 미확정
        self.assertFalse(payments.client_config()["enabled"])
        self.assertEqual(payments.client_config()["clientKey"], "")
        self._set_biz()
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            self.assertTrue(payments.client_config()["enabled"])

    def test_client_config_test_mode_enabled(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        self.assertTrue(payments.client_config()["enabled"])


class HeldProductBrowserGateTest(_EnvCase):
    _HOLD = ("EXPERT", "LIFE_DESIGN", "RELATION_BUSINESS", "ANNUAL_VIP")

    def _set_live_keys(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "live"
        os.environ["TOSS_CLIENT_KEY"] = "live_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "live_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY

    def test_held_live_products_default_and_env_unblock(self):
        held = set(payments.held_live_products())
        self.assertTrue(set(self._HOLD).issubset(held))        # 보류 4종 포함
        self.assertIn("BASIC", held)                           # 전달기한 미확정 출시상품도 포함
        self.assertIn("DEEP", held)
        for c in self._HOLD:
            os.environ["PRODUCT_LIVE_READY_%s" % c] = "true"
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        os.environ["PRODUCT_ETA_DAYS_DEEP"] = "5"
        self.assertEqual(payments.held_live_products(), [])    # 전부 해제되면 빈 목록

    def test_client_config_exposes_held_products_live_only(self):
        self._set_live_keys()
        self.assertIn("EXPERT", payments.client_config()["heldProducts"])
        # test 모드에서는 상품 차단 목록을 노출하지 않는다(상품 가드 미적용).
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_x"
        self.assertEqual(payments.client_config()["heldProducts"], [])

    def test_delivery_eta_single_source_and_client_match(self):
        # 확정 기한은 한 곳(PRODUCT_ETA_DAYS_*)에서 오고, 서버·클라이언트가 같은 값을 쓴다.
        self._set_live_keys()
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        self.assertEqual(payments.product_delivery_eta("BASIC"), "3영업일")
        cfg = payments.client_config()
        self.assertEqual(cfg["deliveryEtas"].get("BASIC"), "3영업일")
        self.assertNotIn("BASIC", cfg["heldProducts"])  # 확정되면 신청 불가 해제

    def test_app_js_renders_held_label_and_single_eta_source(self):
        with open(os.path.join(ROOT, "static", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("현재 신청 불가", js)       # 고객 화면 표시
        self.assertIn("isHeldLive", js)            # 진입 차단 헬퍼
        self.assertIn("heldProducts", js)          # 서버 전달 목록 사용
        self.assertIn("function etaText", js)      # 전달기한 단일 출처 헬퍼
        self.assertIn("deliveryEtas", js)          # 서버 확정 기한 사용
        self.assertIn("esc(etaText(pr))", js)                 # 상품 카드가 단일 출처 사용
        self.assertIn("\"예상 발송 기간\", etaText(pr)", js)   # 주문 안내도 같은 출처 사용


if __name__ == "__main__":
    unittest.main(verbosity=2)
