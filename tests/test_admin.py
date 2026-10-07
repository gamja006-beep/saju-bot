"""읽기 전용 관리자 주문 뷰어 테스트 (실 DB/토스 없이 In-Memory + mock)."""

import os
import sys
import base64
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import saju_bot
import admin
import payments
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode("ascii")
_ADMIN_USER = "alpha"
_ADMIN_PW = "s3cret-pw-2026"
_PAY_ENV = ["PAYMENTS_ENABLED", "PAYMENT_MODE", "TOSS_CLIENT_KEY",
            "TOSS_SECRET_KEY", "ORDER_ENCRYPTION_KEY", "DATABASE_URL"]
_ADMIN_ENV = ["ADMIN_USERNAME", "ADMIN_PASSWORD"]


def _toss_ok(**kw):
    return {"orderId": kw["order_id"], "totalAmount": kw["amount"], "status": "DONE"}


def _auth(u, p):
    token = base64.b64encode(("%s:%s" % (u, p)).encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + token}


class AdminViewerTest(unittest.TestCase):
    def setUp(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_sampleclient"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_samplesecret"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        os.environ["ADMIN_USERNAME"] = _ADMIN_USER
        os.environ["ADMIN_PASSWORD"] = _ADMIN_PW
        self.store = payments.InMemoryOrderStore()
        saju_bot.ORDER_STORE = self.store
        self.c = saju_bot.app.test_client()

    def tearDown(self):
        for k in _PAY_ENV + _ADMIN_ENV:
            os.environ.pop(k, None)

    # ---- helpers ----
    def _make_paid(self, product="EXPERT", email="customer@example.com", payload=None, pk="pk_1"):
        payload = payload or {"consultation_type": "종합", "birth_date": "1990-05-15",
                              "question": "올해 이직해도 될까요?", "topics": ["직업", "재물"]}
        o = payments.create_order(self.store, product, email, payload)
        payments.approve_payment(self.store, pk, o["orderId"], o["amount"], confirm_fn=_toss_ok)
        return o["orderId"]

    def _make_pending(self, email="pending@example.com"):
        o = payments.create_order(self.store, "BASIC", email, {"question": "대기중"})
        return o["orderId"]

    # ---- 인증 ----
    def test_no_auth_401(self):
        r = self.c.get("/admin/orders")
        self.assertEqual(r.status_code, 401)
        self.assertIn("WWW-Authenticate", r.headers)

    def test_wrong_auth_401(self):
        r = self.c.get("/admin/orders", headers=_auth(_ADMIN_USER, "wrong"))
        self.assertEqual(r.status_code, 401)
        r2 = self.c.get("/admin/orders", headers=_auth("nobody", _ADMIN_PW))
        self.assertEqual(r2.status_code, 401)

    def test_missing_env_404(self):
        os.environ.pop("ADMIN_USERNAME", None)
        os.environ.pop("ADMIN_PASSWORD", None)
        r = self.c.get("/admin/orders", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 404)  # 자격 미설정 -> 경로 숨김
        r2 = self.c.get("/admin/orders")
        self.assertEqual(r2.status_code, 404)

    def test_get_only_405_on_post(self):
        r = self.c.post("/admin/orders", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 405)

    # ---- 목록 ----
    def test_list_ok_200_paid_only(self):
        paid = self._make_paid(email="customer@example.com")
        pending = self._make_pending()
        r = self.c.get("/admin/orders", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn(paid, body)
        self.assertNotIn(pending, body)  # PAID 아닌 주문은 미표시

    def test_list_email_masked(self):
        self._make_paid(email="customer@example.com")
        body = self.c.get("/admin/orders", headers=_auth(_ADMIN_USER, _ADMIN_PW)).get_data(as_text=True)
        self.assertIn("cu***@example.com", body)
        self.assertNotIn("customer@example.com", body)  # 목록엔 전체 이메일 미노출

    def test_list_noindex_header_and_meta(self):
        self._make_paid()
        r = self.c.get("/admin/orders", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertIn("noindex", r.headers.get("X-Robots-Tag", ""))
        self.assertIn('name="robots"', r.get_data(as_text=True))
        self.assertIn("noindex", r.get_data(as_text=True))

    def test_list_capped_at_100(self):
        for i in range(101):
            self._make_paid(email="user%d@example.com" % i, pk="pk_%d" % i)
        body = self.c.get("/admin/orders", headers=_auth(_ADMIN_USER, _ADMIN_PW)).get_data(as_text=True)
        self.assertEqual(body.count('href="/admin/orders/ord_'), 100)
        self.assertIn("총 100건", body)

    # ---- 상세 ----
    def test_detail_decrypts_email_and_consultation(self):
        oid = self._make_paid(email="customer@example.com",
                              payload={"consultation_type": "집중", "birth_date": "1991-02-03",
                                       "question": "창업 시기를 알고 싶어요", "topics": ["재물"]})
        r = self.c.get("/admin/orders/%s" % oid, headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("customer@example.com", body)       # 상세는 전체 이메일
        self.assertIn("창업 시기를 알고 싶어요", body)       # 복호화된 상담자료(한국어)
        self.assertIn("집중", body)
        # JSON/암호문/DB 내부값 미노출
        self.assertNotIn("encrypted_", body)
        self.assertNotIn("{\"", body)
        self.assertNotIn("pk_1", body)                     # payment_key 미노출

    def test_detail_non_paid_404(self):
        pending = self._make_pending()
        r = self.c.get("/admin/orders/%s" % pending, headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 404)

    def test_detail_unknown_404(self):
        r = self.c.get("/admin/orders/ord_doesnotexist", headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertEqual(r.status_code, 404)

    def test_detail_noindex(self):
        oid = self._make_paid()
        r = self.c.get("/admin/orders/%s" % oid, headers=_auth(_ADMIN_USER, _ADMIN_PW))
        self.assertIn("noindex", r.headers.get("X-Robots-Tag", ""))

    # ---- XSS / 비밀값 ----
    def test_detail_xss_escaped(self):
        oid = self._make_paid(payload={"consultation_type": "종합",
                                       "question": "<script>alert(1)</script>",
                                       "situation": "<img src=x onerror=alert(2)>"})
        body = self.c.get("/admin/orders/%s" % oid, headers=_auth(_ADMIN_USER, _ADMIN_PW)).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertNotIn("<img src=x onerror=alert(2)>", body)
        self.assertIn("&lt;script&gt;", body)  # 이스케이프 확인

    def test_no_secret_leak_in_pages(self):
        oid = self._make_paid()
        for path in ("/admin/orders", "/admin/orders/%s" % oid):
            body = self.c.get(path, headers=_auth(_ADMIN_USER, _ADMIN_PW)).get_data(as_text=True)
            self.assertNotIn(_ADMIN_PW, body)
            self.assertNotIn(_FERNET_KEY, body)
            self.assertNotIn("samplesecret", body)   # TOSS_SECRET_KEY
            self.assertNotIn("DATABASE_URL", body)

    def test_admin_not_linked_from_customer_ui(self):
        with open(os.path.join(ROOT, "templates", "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("/admin", html)
        with open(os.path.join(ROOT, "static", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("/admin", js)


class AdminStoreTest(unittest.TestCase):
    """스토어의 읽기 전용 list_paid_orders 계약."""

    def setUp(self):
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_x"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_x"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        self.store = payments.InMemoryOrderStore()

    def tearDown(self):
        for k in _PAY_ENV:
            os.environ.pop(k, None)

    def test_list_returns_only_paid(self):
        o1 = payments.create_order(self.store, "BASIC", "a@b.com", {})
        payments.approve_payment(self.store, "pk", o1["orderId"], 9900, confirm_fn=_toss_ok)
        payments.create_order(self.store, "DEEP", "c@d.com", {})  # PENDING
        paid = self.store.list_paid_orders(limit=100)
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid[0]["status"], "PAID")

    def test_list_respects_limit(self):
        for i in range(5):
            o = payments.create_order(self.store, "BASIC", "u%d@b.com" % i, {})
            payments.approve_payment(self.store, "pk%d" % i, o["orderId"], 9900, confirm_fn=_toss_ok)
        self.assertEqual(len(self.store.list_paid_orders(limit=3)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
