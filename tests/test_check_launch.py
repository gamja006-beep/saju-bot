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


def _enable_payments(env, mode="test"):
    env["PAYMENTS_ENABLED"] = "true"
    env["PAYMENT_MODE"] = mode
    env["TOSS_CLIENT_KEY"] = mode + "_ck_sampleclient"
    env["TOSS_SECRET_KEY"] = mode + "_sk_SUPERSECRET_value_123"
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
                "mail_order_no": "2025-고양덕양구-2991",
                "address": "경기도 고양시 덕양구 중앙로558번길 57"}
        self.assertEqual(check_launch.check_business_info(info, {})["status"], check_launch.PASS)

    def test_business_info_blank_address_is_pending(self):
        # 주소 공개 표기가 비면(합성 빈 info) 여전히 차단된다.
        info = {"representative": "오준영", "reg_no": "564-05-02583",
                "mail_order_no": "2025-고양덕양구-2991", "address": ""}
        r = check_launch.check_business_info(info, {})
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("BIZ_ADDRESS", r["detail"])

    def test_business_info_confirmed_defaults_pass(self):
        # 대표자·등록번호·신고번호·주소 공개 표기 모두 확정 기본값으로 통과한다.
        import legal_pages as lp
        info = lp.business_info()
        self.assertEqual(info["representative"], "오준영")
        self.assertEqual(info["reg_no"], "564-05-02583")
        self.assertTrue(info["address"])
        self.assertEqual(check_launch.check_business_info(info, {})["status"], check_launch.PASS)

    def test_contact_confirmed_defaults_pass(self):
        # 이메일·문의 전화 모두 확정 기본값으로 통과한다.
        import legal_pages as lp
        info = lp.business_info()
        self.assertTrue(info["phone"])
        self.assertEqual(check_launch.check_contact(info, {})["status"], check_launch.PASS)

    def test_contact_blank_phone_is_pending(self):
        info = {"email": "help@example.test", "phone": ""}
        r = check_launch.check_contact(info, {})
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("BIZ_PHONE", r["detail"])


class RealArtifactsTest(_EnvCase):
    def test_launch_products_blocked_until_eta_confirmed(self):
        # 환경변수 미설정(_EnvCase) → BASIC·DEEP 전달기한 미확정으로 차단된다.
        self.assertEqual(check_launch.check_launch_products()["status"], check_launch.PENDING)

    def test_held_products_are_hold_not_launch_blocker(self):
        self.assertEqual(check_launch.check_held_products()["status"], check_launch.HOLD)

    def test_real_legal_pages_have_no_pending_markers(self):
        # 출시 준비 완료: 약관·개인정보·환불에 '입력 대기'(미확정)가 없다.
        self.assertEqual(check_launch.check_legal_pending()["status"], check_launch.PASS)
        self.assertFalse(legal_pages.any_pending())
        self.assertFalse(legal_pages.cross_border_pending())

    def test_cross_border_check_is_informational_not_a_blocker(self):
        r = check_launch.check_cross_border_transfer()
        # 정보성(INFO) — 출시 차단 건수에 넣지 않는다(실제 차단은 법적 고지 미확정이 담당).
        self.assertEqual(r["status"], check_launch.INFO)
        # 공식 자료로 확인된 받는 자가 안내에 드러난다.
        self.assertIn("OpenAI OpCo, LLC", r["detail"])
        self.assertIn("Google LLC", r["detail"])
        # 남은 확정 항목(보유기간/모델 학습)을 미확정으로 명시하되 '해결'로 단정하지 않는다.
        self.assertIn("보유", r["detail"])

    def test_cross_border_not_counted_in_launch_blockers(self):
        # run_checks 에 정보성 국외이전 항목이 포함되지만 차단 건수(PENDING)에는 들어가지 않는다.
        results = check_launch.run_checks()
        keys = {r["key"] for r in results}
        self.assertIn("cross_border", keys)
        blockers = [r["key"] for r in results if r["status"] == check_launch.PENDING]
        self.assertNotIn("cross_border", blockers)


class DevEnvBlockersTest(_EnvCase):
    def test_empty_env_reports_blockers_and_exit_1(self):
        results = check_launch.run_checks()
        by = {r["key"]: r["status"] for r in results}
        # 사업자 표시·문의처는 운영자 확정 기본값으로 통과한다.
        self.assertEqual(by["business_info"], check_launch.PASS)
        self.assertEqual(by["contact"], check_launch.PASS)
        # 법적 고지는 확정 완료로 통과한다(국외 이전 보유·이용 반영, '입력 대기' 없음).
        self.assertEqual(by["legal"], check_launch.PASS)
        # 결제·전달기한은 환경변수 미설정이라 여전히 미완료(차단).
        for key in ("payment", "launch_products"):
            self.assertEqual(by[key], check_launch.PENDING, key)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = check_launch.main()
        self.assertEqual(rc, 1)
        self.assertIn("출시 차단", buf.getvalue())


class AllPassTest(_EnvCase):
    def test_fully_configured_synthetic_has_no_blockers(self):
        # '실제 유료 출시 준비 완료'는 live 모드 + 라이브 키일 때만 차단 0건이 된다.
        _fill_business(os.environ)
        _enable_payments(os.environ, "live")
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        os.environ["PRODUCT_ETA_DAYS_DEEP"] = "5"
        clean_doc = {"sections": [{"h": "x", "p": ["확정된 문구"], "li": []}]}
        with mock.patch.object(legal_pages, "document", return_value=clean_doc):
            results = check_launch.run_checks()
        # 출시 차단(PENDING)이 하나도 없어야 한다. 보류 상품(HOLD)은 의도적 상태로 허용.
        pending = [r for r in results if r["status"] == check_launch.PENDING]
        self.assertEqual(pending, [], pending)
        self.assertTrue(any(r["status"] == check_launch.HOLD for r in results))

    def test_test_mode_valid_keys_not_launch_ready(self):
        # PAYMENTS_ENABLED + 유효한 test 키라도 '실제 유료 출시 준비 완료'로 통과하지 않는다.
        _enable_payments(os.environ, "test")
        r = check_launch.check_payment_config()
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("테스트 모드", r["detail"])

    def test_live_mode_matching_keys_is_launch_ready(self):
        _enable_payments(os.environ, "live")
        r = check_launch.check_payment_config()
        self.assertEqual(r["status"], check_launch.PASS)

    def test_live_mode_key_prefix_mismatch_blocks(self):
        # live 모드인데 키 접두사가 test_ 면 혼용 차단은 유지된다.
        _enable_payments(os.environ, "live")
        os.environ["TOSS_SECRET_KEY"] = "test_sk_wrongmode"
        r = check_launch.check_payment_config()
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("접두사", r["detail"])

    def test_widget_keys_rejected(self):
        # 결제위젯 키(gck/gsk)는 TossPayments.payment() 개별 연동 키가 아니므로 거부한다.
        _enable_payments(os.environ, "live")
        os.environ["TOSS_CLIENT_KEY"] = "live_gck_widgetclient"
        os.environ["TOSS_SECRET_KEY"] = "live_gsk_widgetsecret"
        r = check_launch.check_payment_config()
        self.assertEqual(r["status"], check_launch.PENDING)
        self.assertIn("종류", r["detail"])
        self.assertFalse(payments.config_status()["keys_match_mode"])
        self.assertFalse(payments.payments_enabled())

    def test_swapped_client_secret_keys_rejected(self):
        # 클라이언트 자리에 시크릿 키(sk), 시크릿 자리에 클라이언트 키(ck)를 넣으면 거부한다.
        _enable_payments(os.environ, "live")
        os.environ["TOSS_CLIENT_KEY"] = "live_sk_secretinclientslot"
        os.environ["TOSS_SECRET_KEY"] = "live_ck_clientinsecretslot"
        self.assertEqual(check_launch.check_payment_config()["status"], check_launch.PENDING)
        self.assertFalse(payments.payments_enabled())

    def test_client_must_be_ck_secret_must_be_sk(self):
        # 올바른 종류 쌍(live_ck_ / live_sk_)만 통과한다.
        _enable_payments(os.environ, "live")  # 기본이 live_ck_/live_sk_ 쌍
        self.assertTrue(payments.config_status()["keys_match_mode"])
        # 클라이언트가 sk 종류면 거부
        os.environ["TOSS_CLIENT_KEY"] = "live_sk_notaclientkey"
        self.assertFalse(payments.config_status()["keys_match_mode"])


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

    def test_launch_blockers_empty_when_biz_and_legal_confirmed(self):
        # 주소·전화는 확정 기본값으로 통과하고, 법적 고지도 확정 완료되어 공통 차단이 없다.
        joined = " ".join(payments.launch_blockers())
        self.assertNotIn("BIZ_ADDRESS", joined)
        self.assertNotIn("BIZ_PHONE", joined)
        self.assertEqual(payments.launch_blockers(), [])

    def test_launch_blockers_still_block_if_legal_regresses(self):
        # 가드 자체는 유효하다: 법적 고지가 다시 미확정이 되면 공통 차단으로 돌아간다.
        self._set_biz()
        self.assertEqual(payments.launch_blockers(), [])  # 현재(확정 완료)는 공통 차단 없음
        with mock.patch.object(legal_pages, "any_pending", return_value=True):
            self.assertIn("법적 고지", " ".join(payments.launch_blockers()))

    def test_live_blocked_by_each_condition(self):
        self._set_live_keys()
        self.assertTrue(payments.payments_enabled())
        # (1) 주소·전화 확정 기본값 + 법적 고지 확정 완료 → 공통 조건 충족
        self.assertTrue(payments.live_payments_ready())
        self._set_biz()
        self.assertTrue(payments.live_payments_ready())
        # (2) 가드 유효성: 법적 고지가 다시 미확정이 되면 라이브 공통 차단으로 돌아간다
        with mock.patch.object(legal_pages, "any_pending", return_value=True):
            self.assertFalse(payments.live_payments_ready())

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

    def test_api_orders_common_block_live_when_legal_incomplete(self):
        # 공통 라이브 가드 유효성: 법적 고지가 미확정이면 상품 가드 이전에 라이브를 차단한다.
        import saju_bot
        self._set_live_keys()
        c = saju_bot.app.test_client()
        with mock.patch.object(legal_pages, "any_pending", return_value=True):
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
            r = c.post("/api/orders", json={"product_code": "BASIC", "email": "x@example.test",
                                            "agree_cross_border": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("status"), "ok")

    def test_api_orders_live_blocked_without_cross_border_consent(self):
        # 라이브 공통·상품·전달기한 가드를 모두 통과해도, 국외 이전 동의가 없으면 거부한다.
        import saju_bot
        self._set_live_keys()
        self._set_biz()
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        c = saju_bot.app.test_client()
        with mock.patch.object(legal_pages, "any_pending", return_value=False):
            r = c.post("/api/orders", json={"product_code": "BASIC", "email": "x@example.test"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json().get("code"), "consent_required")
        self.assertEqual(saju_bot.ORDER_STORE.calls, 0)  # 동의 없으면 주문을 만들지 않는다

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
        self._set_live_keys()
        # 가드 유효성: 법적 고지가 미확정이면 브라우저 결제 설정이 비활성(키 비노출).
        with mock.patch.object(legal_pages, "any_pending", return_value=True):
            self.assertFalse(payments.client_config()["enabled"])
            self.assertEqual(payments.client_config()["clientKey"], "")
        # 확정 완료(현재 기본 상태) → 라이브 공통 조건 충족으로 활성.
        self._set_biz()
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
        self.assertEqual(payments.product_delivery_eta("BASIC"), "3영업일 이내")
        cfg = payments.client_config()
        self.assertEqual(cfg["deliveryEtas"].get("BASIC"), "3영업일 이내")
        self.assertNotIn("BASIC", cfg["heldProducts"])  # 확정되면 신청 불가 해제

    def test_confirmed_basic3_deep5_etas(self):
        # 운영자 확정값(BASIC 3·DEEP 5영업일 이내)을 단일 출처로 동일하게 표시한다.
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        os.environ["PRODUCT_ETA_DAYS_DEEP"] = "5"
        self.assertEqual(payments.product_delivery_eta("BASIC"), "3영업일 이내")
        self.assertEqual(payments.product_delivery_eta("DEEP"), "5영업일 이내")
        self.assertIsNone(payments.product_live_blocked("BASIC"))
        self.assertIsNone(payments.product_live_blocked("DEEP"))

    def test_delivery_note_present_and_no_forbidden_claims(self):
        with open(os.path.join(ROOT, "static", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        note = "명식 확인·해석·검토 후 이메일로 보내 드려 시간이 걸립니다(즉시 자동 발송 아님)."
        self.assertIn("DELIVERY_NOTE", js)
        self.assertIn(note, js)                         # 소요 시간 설명(단일 문구)
        self.assertNotIn("전문가", note)                # 안내 문구에 전문가 표현 없음
        self.assertNotIn("즉시 자동 발송됩니다", js)     # 즉시 자동 발송이라고 단정하지 않음
        self.assertIn("esc(DELIVERY_NOTE)", js)         # 상품 카드에 표시
        self.assertIn("fr-note\", DELIVERY_NOTE", js)   # 결제 직전 확인 화면에 표시

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
