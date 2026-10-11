"""BASIC·DEEP 첫 유료 출시 합성 end-to-end 검증.

실결제·실메일·실 n8n 없이(토스 confirm 과 n8n POST 를 monkeypatch) 전체 고객 흐름을 재현한다:
무료 결과 → 주문 생성 → 토스 테스트 승인 → PAID 저장 → 운영자 알림(outbox) →
관리자 자료 복사 → 받는 사람 이메일 확인 → 수동 발송(발송 완료로 기록) → 발송 완료 상태.
중복 주문·알림, 금액 불일치·토스 실패(미결제 유지), 발송 기록 취소(환불/정정 대비),
모바일 뷰포트까지 함께 확인한다. 실제 개인정보가 아닌 합성 입력만 사용한다.
"""

import base64
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import payments
import notifier
import admin
import saju_bot
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode("ascii")
_ENV = ["PAYMENTS_ENABLED", "PAYMENT_MODE", "TOSS_CLIENT_KEY", "TOSS_SECRET_KEY",
        "ORDER_ENCRYPTION_KEY", "DATABASE_URL",
        "OPERATOR_NOTIFICATIONS_ENABLED", "N8N_NOTIFICATION_WEBHOOK_URL",
        "N8N_NOTIFICATION_HEADER_SECRET",
        "ADMIN_USERNAME", "ADMIN_PASSWORD",
        "PRODUCT_ETA_DAYS_BASIC", "PRODUCT_ETA_DAYS_DEEP"]

_ADMIN_USER = "operator"
_ADMIN_PASS = "launch-pass-123"


def _toss_ok(**kw):
    return {"orderId": kw["order_id"], "totalAmount": kw["amount"],
            "status": "DONE", "paymentKey": kw["payment_key"]}


def _n8n_ok(body):
    return (200, {"event_id": body["event_id"], "order_id": body["order_id"],
                  "channel": body["channel"], "status": "sent"})


def _auth():
    raw = ("%s:%s" % (_ADMIN_USER, _ADMIN_PASS)).encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


class LaunchE2ETest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV}
        for k in _ENV:
            os.environ.pop(k, None)
        os.environ["PAYMENTS_ENABLED"] = "true"
        os.environ["PAYMENT_MODE"] = "test"
        os.environ["TOSS_CLIENT_KEY"] = "test_ck_launchclient"
        os.environ["TOSS_SECRET_KEY"] = "test_sk_launchsecret"
        os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY
        os.environ["OPERATOR_NOTIFICATIONS_ENABLED"] = "true"
        os.environ["N8N_NOTIFICATION_WEBHOOK_URL"] = "https://n8n.example.test/webhook/x"
        os.environ["N8N_NOTIFICATION_HEADER_SECRET"] = "notify-secret-xyz"
        os.environ["ADMIN_USERNAME"] = _ADMIN_USER
        os.environ["ADMIN_PASSWORD"] = _ADMIN_PASS
        os.environ["PRODUCT_ETA_DAYS_BASIC"] = "3"
        os.environ["PRODUCT_ETA_DAYS_DEEP"] = "5"

        self.store = payments.InMemoryOrderStore()
        saju_bot.ORDER_STORE = self.store
        self.c = saju_bot.app.test_client()

        # 네트워크 차단: 토스 confirm 과 n8n POST 를 합성 응답으로 대체.
        self._saved_toss = payments._toss_confirm
        self._saved_http = notifier._http_post
        payments._toss_confirm = _toss_ok
        notifier._http_post = lambda url, body, secret, timeout=10: _n8n_ok(body)

    def tearDown(self):
        payments._toss_confirm = self._saved_toss
        notifier._http_post = self._saved_http
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- 흐름 구성 요소 ---
    def _free_result(self):
        before = self.store.calls
        r = self.c.post("/free-insights", json={
            "calendar": "solar", "birth_date": "1990-05-15", "birth_time": "08:30",
            "gender": "남", "alias": "바다", "topics": ["재물", "직업"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")
        self.assertEqual(self.store.calls, before, "무료 결과는 DB 를 호출하지 않아야 한다")

    def _create_order(self, code, email):
        r = self.c.post("/api/orders", json={
            "product_code": code, "email": email,
            "calendar": "solar", "birth_date": "1990-05-15", "birth_time": "08:30",
            "gender": "남", "consultation_type": "종합",
            "question": "올해 이직 적기?", "topics": ["직업", "재물"]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["orderId"].startswith("ord_"))
        return body["orderId"], body["amount"]

    def _pay_success(self, order_id, amount, pk="pk_launch_1"):
        return self.c.get("/payment/success?paymentKey=%s&orderId=%s&amount=%d"
                           % (pk, order_id, amount))

    def _run_happy_chain(self, code, expect_amount, expect_eta, email):
        # 1) 무료 결과 (DB 호출 0)
        self._free_result()
        # 2) 주문 생성 (서버 금액 정본)
        oid, amount = self._create_order(code, email)
        self.assertEqual(amount, expect_amount)
        self.assertEqual(self.store.get_order(oid)["status"], "PENDING")
        # 3) 토스 테스트 승인 → 4) PAID 저장
        r = self._pay_success(oid, amount)
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn("결제가 확인되었습니다", page)
        self.assertIn(expect_eta, page)                 # 서버 단일 출처 ETA(3/5영업일 이내)
        self.assertIn(oid, page)                        # 문의용 주문번호
        self.assertEqual(self.store.get_order(oid)["status"], "PAID")
        # 5) 운영자 알림 outbox 전송 완료(두 채널)
        notes = self.store.get_notifications_for_order(oid)
        self.assertEqual(len(notes), len(payments.NOTIFY_CHANNELS))
        self.assertTrue(all(n["status"] == "SENT" for n in notes), notes)
        # 6) 관리자 자료 복사 + 받는 사람 이메일 확인
        d = self.c.get("/admin/orders/%s" % oid, headers=_auth())
        self.assertEqual(d.status_code, 200)
        detail = d.get_data(as_text=True)
        self.assertIn("[고객 입력 시작]", detail)        # 복사용 자료 본문
        self.assertIn(email, detail)                    # 받는 사람 전체 이메일(발송 확인용)
        self.assertIn("발송 완료로 기록", detail)
        # 7) 수동 발송 후 '발송 완료로 기록'(상태만 저장, 메일 전송 아님)
        token = admin._report_csrf_token(oid)
        m = self.c.post("/admin/orders/%s/report/mark-sent" % oid,
                        data={"csrf_token": token}, headers=_auth())
        self.assertEqual(m.status_code, 200)
        self.assertIsNotNone(self.store.get_order(oid).get("report_sent_at"))
        # 발송 완료 → 대기 목록에서 빠지고 완료 목록에 들어간다
        waiting = [o["order_id"] for o in self.store.list_paid_orders(dispatch="waiting")]
        done = [o["order_id"] for o in self.store.list_paid_orders(dispatch="done")]
        self.assertNotIn(oid, waiting)
        self.assertIn(oid, done)
        return oid

    # --- 전체 happy path: BASIC, DEEP ---
    def test_basic_full_chain(self):
        self._run_happy_chain("BASIC", 9900, "3영업일 이내", "basic-buyer@example.test")

    def test_deep_full_chain(self):
        self._run_happy_chain("DEEP", 39000, "5영업일 이내", "deep-buyer@example.test")

    # --- 중복 주문·알림 ---
    def test_duplicate_payment_is_idempotent_and_no_double_notify(self):
        oid = self._run_happy_chain("BASIC", 9900, "3영업일 이내", "dup@example.test")
        notes_before = self.store.get_notifications_for_order(oid)
        # 같은 결제키로 재승인 → 멱등(PAID 유지), 알림 중복 생성 없음
        r = self._pay_success(oid, 9900)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.store.get_order(oid)["status"], "PAID")
        notes_after = self.store.get_notifications_for_order(oid)
        self.assertEqual(len(notes_after), len(notes_before))
        self.assertTrue(all(n["status"] == "SENT" for n in notes_after))
        # 발송 완료 재기록도 멱등(한 번만 기록)
        token = admin._report_csrf_token(oid)
        again = self.c.post("/admin/orders/%s/report/mark-sent" % oid,
                            data={"csrf_token": token}, headers=_auth())
        self.assertEqual(again.status_code, 200)
        self.assertIn("이미 발송", again.get_data(as_text=True))

    # --- 실패 경로: 금액 불일치는 결제 실패(미결제 유지) ---
    def test_amount_mismatch_shows_fail_and_not_paid(self):
        oid, amount = self._create_order("BASIC", "mismatch@example.test")
        r = self._pay_success(oid, amount + 1)          # 위조 금액
        self.assertEqual(r.status_code, 400)
        self.assertNotEqual(self.store.get_order(oid)["status"], "PAID")

    # --- 실패 경로: 토스 승인 실패는 FAILED, 알림 없음 ---
    def test_toss_failure_marks_failed_no_notify(self):
        oid, amount = self._create_order("DEEP", "tossdown@example.test")

        def _boom(**kw):
            raise RuntimeError("toss down")
        payments._toss_confirm = _boom
        r = self._pay_success(oid, amount)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.store.get_order(oid)["status"], "FAILED")
        self.assertEqual(self.store.get_notifications_for_order(oid), [])

    # --- 환불/정정 대비: 발송 완료 기록 취소 ---
    def test_unmark_after_sent_returns_to_waiting(self):
        oid = self._run_happy_chain("BASIC", 9900, "3영업일 이내", "refund@example.test")
        token = admin._report_unmark_csrf_token(oid)
        u = self.c.post("/admin/orders/%s/report/unmark-sent" % oid,
                        data={"csrf_token": token}, headers=_auth())
        self.assertEqual(u.status_code, 200)
        self.assertIsNone(self.store.get_order(oid).get("report_sent_at"))
        waiting = [o["order_id"] for o in self.store.list_paid_orders(dispatch="waiting")]
        self.assertIn(oid, waiting)

    # --- 보류 4종은 test 흐름과 무관하게 주문 코드로는 유효하나 1차 출시는 BASIC·DEEP 만 ---
    def test_free_product_cannot_be_ordered(self):
        r = self.c.post("/api/orders", json={"product_code": "FREE", "email": "x@example.test"})
        self.assertEqual(r.status_code, 400)

    # --- 모바일 뷰포트: 결제 성공 페이지가 모바일 대응 ---
    def test_success_page_mobile_viewport(self):
        oid, amount = self._create_order("BASIC", "mobile@example.test")
        page = self._pay_success(oid, amount).get_data(as_text=True)
        self.assertIn("width=device-width", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
