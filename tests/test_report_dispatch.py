"""수동 보고서 발송 관리 테스트.

실 DB·실메일·실결제 없이 InMemory 저장소 + 모의 토스로 검증한다.
완료 시각만 기록하고 보고서 본문·PDF·평문 이메일은 새로 저장하지 않는다.
"""

import os
import sys
import base64
import datetime
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import payments
import saju_bot
import admin
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode("ascii")
_ADMIN_USER, _ADMIN_PW = "alpha", "s3cret-pw-2026"
_ENV = ["PAYMENTS_ENABLED", "PAYMENT_MODE", "TOSS_CLIENT_KEY", "TOSS_SECRET_KEY",
        "ORDER_ENCRYPTION_KEY", "ADMIN_USERNAME", "ADMIN_PASSWORD",
        "PRODUCT_ETA_DAYS_BASIC", "PRODUCT_ETA_DAYS_DEEP"]


def _now():
    return datetime.datetime(2026, 10, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _toss_ok(**kw):
    return {"orderId": kw["order_id"], "totalAmount": kw["amount"], "status": "DONE"}


def _auth(u=_ADMIN_USER, p=_ADMIN_PW):
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (u, p)).encode()).decode()}


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV}
        for k in _ENV:
            os.environ.pop(k, None)
        os.environ.update({"PAYMENTS_ENABLED": "true", "PAYMENT_MODE": "test",
                           "TOSS_CLIENT_KEY": "test_ck_x", "TOSS_SECRET_KEY": "test_sk_x",
                           "ORDER_ENCRYPTION_KEY": _FERNET_KEY,
                           "ADMIN_USERNAME": _ADMIN_USER, "ADMIN_PASSWORD": _ADMIN_PW,
                           "PRODUCT_ETA_DAYS_BASIC": "3", "PRODUCT_ETA_DAYS_DEEP": "5"})
        self.store = payments.InMemoryOrderStore()
        saju_bot.ORDER_STORE = self.store
        self.c = saju_bot.app.test_client()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _paid(self, code="BASIC", pk="pk_1", when=None):
        o = payments.create_order(self.store, code, "customer@example.com",
                                  {"birth_date": "1990-05-15"})
        payments.approve_payment(self.store, pk, o["orderId"], o["amount"], confirm_fn=_toss_ok)
        if when is not None:
            self.store.orders[o["orderId"]]["paid_at"] = when
        return o["orderId"]


# ---- 1. 저장소 멱등 기록 ----
class StoreMarkSentTest(_Base):
    def test_mark_sent_sets_once_then_idempotent(self):
        oid = self._paid()
        changed, sent_at = self.store.mark_report_sent(oid, _now())
        self.assertTrue(changed)
        self.assertEqual(sent_at, _now())
        self.assertEqual(self.store.get_order(oid)["report_sent_at"], _now())
        # 두 번째 호출(중복 클릭): 변경 없음, 시각 유지.
        later = _now() + datetime.timedelta(hours=1)
        changed2, sent_at2 = self.store.mark_report_sent(oid, later)
        self.assertFalse(changed2)
        self.assertEqual(sent_at2, _now())
        self.assertEqual(self.store.get_order(oid)["report_sent_at"], _now())

    def test_non_paid_not_marked(self):
        o = payments.create_order(self.store, "BASIC", "c@example.com", {"birth_date": "1990-01-01"})
        changed, _ = self.store.mark_report_sent(o["orderId"], _now())  # 아직 PENDING
        self.assertFalse(changed)
        self.assertIsNone(self.store.get_order(o["orderId"]).get("report_sent_at"))

    def test_unknown_order_not_marked(self):
        self.assertEqual(self.store.mark_report_sent("ord_nope", _now()), (False, None))

    def test_only_timestamp_stored_no_report_body(self):
        oid = self._paid()
        self.store.mark_report_sent(oid, _now())
        rec = self.store.orders[oid]
        self.assertIn("report_sent_at", rec)
        for bad in ("report_text", "report_body", "pdf", "email_plain", "report_html"):
            self.assertNotIn(bad, rec)


# ---- 2. Postgres SQL 가드(모의 연결) ----
class _FakeCur:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if sql.strip().startswith("UPDATE orders SET report_sent_at"):
            self.rowcount = self.conn.update_rowcount
        elif sql.strip().startswith("SELECT"):
            self.conn.last_select = sql

    def fetchone(self):
        return self.conn.row


class _FakeConn:
    def __init__(self, row=None, update_rowcount=1):
        self.executed = []
        self.row = row
        self.update_rowcount = update_rowcount

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCur(self)


class PgMarkSentSqlTest(unittest.TestCase):
    def _store(self, fake):
        s = payments.PostgresOrderStore.__new__(payments.PostgresOrderStore)
        s._connect = lambda: fake
        return s

    def test_update_guards_paid_and_null(self):
        row = ("ord_x", "BASIC", 9900, "KRW", "PAID", "pk", None, None, None, None, _now())
        fake = _FakeConn(row=row, update_rowcount=1)
        store = self._store(fake)
        changed, sent_at = store.mark_report_sent("ord_x", _now())
        self.assertTrue(changed)
        upd = [s for (s, _) in fake.executed if s.strip().startswith("UPDATE orders SET report_sent_at")][0]
        self.assertIn("status='PAID'", upd)
        self.assertIn("report_sent_at IS NULL", upd)

    def test_update_no_row_means_already_or_not_eligible(self):
        row = ("ord_x", "BASIC", 9900, "KRW", "PAID", "pk", None, None, None, None, _now())
        fake = _FakeConn(row=row, update_rowcount=0)  # 이미 완료 등
        changed, _ = self._store(fake).mark_report_sent("ord_x", _now())
        self.assertFalse(changed)


# ---- 3. 관리자 목록·상세 ----
class AdminViewTest(_Base):
    def test_list_oldest_first_with_dispatch_and_eta(self):
        old = self._paid("BASIC", "pk_old", when=_now() - datetime.timedelta(days=2))
        new = self._paid("DEEP", "pk_new", when=_now())
        body = self.c.get("/admin/orders", headers=_auth()).get_data(as_text=True)
        self.assertLess(body.index(old), body.index(new))  # 오래된 결제가 먼저
        self.assertIn("발송 대기", body)
        self.assertIn("3영업일 이내", body)   # BASIC 안내
        self.assertIn("5영업일 이내", body)   # DEEP 안내

    def test_detail_shows_dispatch_section_and_form(self):
        oid = self._paid("BASIC")
        body = self.c.get("/admin/orders/%s" % oid, headers=_auth()).get_data(as_text=True)
        self.assertIn("보고서 발송 관리", body)
        self.assertIn("발송 완료로 기록", body)
        self.assertIn("csrf_token", body)
        self.assertIn("3영업일 이내", body)
        self.assertIn("이메일·PDF를 발송하지 않습니다", body)


# ---- 4. 발송 완료 기록 라우트(인증·CSRF·중복·PAID 전용) ----
class MarkSentRouteTest(_Base):
    def _url(self, oid):
        return "/admin/orders/%s/report/mark-sent" % oid

    def _token(self, oid):
        return admin._report_csrf_token(oid)

    def test_requires_auth(self):
        oid = self._paid()
        r = self.c.post(self._url(oid), data={"csrf_token": self._token(oid)})
        self.assertEqual(r.status_code, 401)

    def test_get_not_allowed(self):
        oid = self._paid()
        self.assertEqual(self.c.get(self._url(oid), headers=_auth()).status_code, 405)

    def test_missing_or_bad_csrf_rejected(self):
        oid = self._paid()
        self.assertEqual(self.c.post(self._url(oid), headers=_auth()).status_code, 400)
        r = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": "wrong"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.store.get_order(oid).get("report_sent_at"))  # 기록 안 됨

    def test_valid_records_once_and_duplicate_is_idempotent(self):
        oid = self._paid()
        r1 = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        self.assertEqual(r1.status_code, 200)
        self.assertIn("발송 완료로 기록했습니다", r1.get_data(as_text=True))
        first = self.store.get_order(oid)["report_sent_at"]
        self.assertIsNotNone(first)
        # 중복 제출: 시각 불변 + 안내 메시지.
        r2 = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        self.assertEqual(r2.status_code, 200)
        self.assertIn("이미 발송 완료", r2.get_data(as_text=True))
        self.assertEqual(self.store.get_order(oid)["report_sent_at"], first)

    def test_non_paid_order_404(self):
        o = payments.create_order(self.store, "BASIC", "c@example.com", {"birth_date": "1990-01-01"})
        oid = o["orderId"]  # PENDING
        r = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        self.assertEqual(r.status_code, 404)

    def test_mark_sent_does_not_send_email_or_notify(self):
        # 발송 완료 기록은 외부 발송(토스/알림)을 호출하지 않는다.
        oid = self._paid()
        import notifier
        calls = []
        saved = notifier._http_post
        notifier._http_post = lambda *a, **k: calls.append(1) or (200, {})
        try:
            self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        finally:
            notifier._http_post = saved
        self.assertEqual(calls, [])


# ---- 5. 발송 완료 정정(저장소) ----
class StoreUnmarkSentTest(_Base):
    def test_unmark_reverts_once_then_idempotent(self):
        oid = self._paid()
        self.store.mark_report_sent(oid, _now())
        self.assertIsNotNone(self.store.get_order(oid)["report_sent_at"])
        self.assertTrue(self.store.unmark_report_sent(oid, _now()))
        self.assertIsNone(self.store.get_order(oid)["report_sent_at"])
        # 재요청(이미 대기): 변경 없음.
        self.assertFalse(self.store.unmark_report_sent(oid, _now()))

    def test_unmark_noop_when_never_sent(self):
        oid = self._paid()
        self.assertFalse(self.store.unmark_report_sent(oid, _now()))

    def test_unmark_non_paid_and_unknown(self):
        o = payments.create_order(self.store, "BASIC", "c@example.com", {"birth_date": "1990-01-01"})
        self.assertFalse(self.store.unmark_report_sent(o["orderId"], _now()))  # PENDING
        self.assertFalse(self.store.unmark_report_sent("ord_nope", _now()))

    def test_unmark_keeps_paid_status_and_amount(self):
        oid = self._paid()
        self.store.mark_report_sent(oid, _now())
        before = self.store.get_order(oid)
        self.store.unmark_report_sent(oid, _now())
        after = self.store.get_order(oid)
        self.assertEqual(after["status"], "PAID")
        self.assertEqual(after["amount"], before["amount"])


class PgUnmarkSqlTest(unittest.TestCase):
    def _store(self, fake):
        s = payments.PostgresOrderStore.__new__(payments.PostgresOrderStore)
        s._connect = lambda: fake
        return s

    def test_update_guards_paid_and_not_null(self):
        fake = _FakeConn(row=None, update_rowcount=1)
        self.assertTrue(self._store(fake).unmark_report_sent("ord_x", _now()))
        upd = [s for (s, _) in fake.executed if "report_sent_at=NULL" in s][0]
        self.assertIn("status='PAID'", upd)
        self.assertIn("report_sent_at IS NOT NULL", upd)

    def test_no_row_means_already_waiting(self):
        fake = _FakeConn(row=None, update_rowcount=0)
        self.assertFalse(self._store(fake).unmark_report_sent("ord_x", _now()))


# ---- 6. 발송 완료 정정(관리자 라우트) ----
class UnmarkRouteTest(_Base):
    def _url(self, oid):
        return "/admin/orders/%s/report/unmark-sent" % oid

    def _token(self, oid):
        return admin._report_unmark_csrf_token(oid)

    def _paid_sent(self, code="BASIC"):
        oid = self._paid(code)
        self.store.mark_report_sent(oid, _now())
        return oid

    def test_requires_auth(self):
        oid = self._paid_sent()
        r = self.c.post(self._url(oid), data={"csrf_token": self._token(oid)})
        self.assertEqual(r.status_code, 401)

    def test_get_not_allowed(self):
        oid = self._paid_sent()
        self.assertEqual(self.c.get(self._url(oid), headers=_auth()).status_code, 405)

    def test_missing_or_bad_csrf_rejected(self):
        oid = self._paid_sent()
        self.assertEqual(self.c.post(self._url(oid), headers=_auth()).status_code, 400)
        r = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": "wrong"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNotNone(self.store.get_order(oid)["report_sent_at"])  # 정정 안 됨

    def test_mark_token_not_accepted_for_unmark(self):
        # 기록용 토큰으로는 정정이 되지 않아야 한다(토큰 분리).
        oid = self._paid_sent()
        r = self.c.post(self._url(oid), headers=_auth(),
                        data={"csrf_token": admin._report_csrf_token(oid)})
        self.assertEqual(r.status_code, 400)

    def test_valid_reverts_once_and_duplicate_safe(self):
        oid = self._paid_sent()
        r1 = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        self.assertEqual(r1.status_code, 200)
        self.assertIn("발송 대기", r1.get_data(as_text=True))
        self.assertIsNone(self.store.get_order(oid)["report_sent_at"])
        # 재요청: 안전 처리.
        r2 = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        self.assertEqual(r2.status_code, 200)
        self.assertIn("이미", r2.get_data(as_text=True))
        self.assertIn("상태입니다", r2.get_data(as_text=True))  # 재요청 안전 메시지
        self.assertIsNone(self.store.get_order(oid)["report_sent_at"])

    def test_non_paid_order_404(self):
        o = payments.create_order(self.store, "BASIC", "c@example.com", {"birth_date": "1990-01-01"})
        oid = o["orderId"]  # PENDING
        r = self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        self.assertEqual(r.status_code, 404)

    def test_unmark_does_not_send_or_change_payment(self):
        oid = self._paid_sent()
        import notifier
        calls = []
        saved = notifier._http_post
        notifier._http_post = lambda *a, **k: calls.append(1) or (200, {})
        try:
            self.c.post(self._url(oid), headers=_auth(), data={"csrf_token": self._token(oid)})
        finally:
            notifier._http_post = saved
        self.assertEqual(calls, [])
        self.assertEqual(self.store.get_order(oid)["status"], "PAID")  # 결제 상태 불변

    def test_detail_shows_correct_button_by_state(self):
        oid = self._paid("BASIC")
        waiting = self.c.get("/admin/orders/%s" % oid, headers=_auth()).get_data(as_text=True)
        self.assertIn("발송 완료로 기록", waiting)
        self.assertNotIn("발송 완료 기록 정정", waiting)
        self.store.mark_report_sent(oid, _now())
        sent = self.c.get("/admin/orders/%s" % oid, headers=_auth()).get_data(as_text=True)
        self.assertIn("발송 완료 기록 정정", sent)
        self.assertIn("바꾸지 않습니다", sent)   # 확인 안내(이메일·환불·결제 상태 불변)
        self.assertIn("csrf_token", sent)


# ---- 7. 관리자 목록 상단 안내(결제 후 보고서 처리 흐름) ----
class AdminGuideTest(_Base):
    # 화살표 흐름의 단계 라벨(목록·상세 공통).
    _STEPS = ["1. 결제 확인", "2. 자료 복사", "3. 보고서 작성",
              "4. 내용·이메일 확인", "5. 고객 이메일 발송", "6. 발송 기록"]

    def _body(self):
        r = self.c.get("/admin/orders", headers=_auth())
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def _detail_body(self):
        o = payments.create_order(self.store, "BASIC", "guide@example.test", {"birth_date": "1990-05-15"})
        payments.approve_payment(self.store, "pk_g", o["orderId"], o["amount"], confirm_fn=_toss_ok)
        return self.c.get("/admin/orders/%s" % o["orderId"], headers=_auth()).get_data(as_text=True)

    def test_requires_auth(self):
        self.assertEqual(self.c.get("/admin/orders").status_code, 401)  # 관리자 전용

    def test_flow_shows_arrow_steps_and_notes(self):
        body = self._body()  # 주문이 없어도 흐름은 항상 표시
        self.assertIn("결제 후 보고서 처리 순서", body)
        for step in self._STEPS:
            self.assertIn(step, body)
        self.assertIn("→", body)  # 화살표 흐름
        # 설명(hover/focus/tap 으로 표시)이 단계에 담겨 있다.
        self.assertIn("data-desc=", body)
        self.assertIn("'최종 보고서 생성자료 복사'를 누릅니다", body)
        # 주의사항 3종.
        self.assertIn("'운영자 알림'은 새 주문 알림이며 고객 보고서 발송과 별개입니다.", body)
        self.assertIn("'발송 완료로 기록'은 상태만 저장하며 이메일을 보내지 않습니다.", body)
        self.assertIn("실제 고객 이메일 발송을 확인한 뒤에만", body)

    def test_flow_identical_on_list_and_detail(self):
        # 목록과 상세가 같은 흐름·설명을 쓰므로 문구가 달라지지 않는다(공통 partial).
        fragment = ('data-desc="주문 상세에서 \'최종 보고서 생성자료 복사\'를 누릅니다.">'
                    '2. 자료 복사</button>')
        list_body = self._body()
        detail_body = self._detail_body()
        self.assertIn(fragment, list_body)
        self.assertIn(fragment, detail_body)
        for step in self._STEPS:
            self.assertIn(step, detail_body)

    def test_guide_eta_uses_single_source_when_set(self):
        body = self._body()  # _Base 가 PRODUCT_ETA_DAYS 3/5 설정
        self.assertIn("기본 해석 3영업일 이내", body)
        self.assertIn("심층 보고서 5영업일 이내", body)

    def test_guide_eta_not_fabricated_when_unset(self):
        os.environ.pop("PRODUCT_ETA_DAYS_BASIC", None)
        os.environ.pop("PRODUCT_ETA_DAYS_DEEP", None)
        body = self._body()
        self.assertIn("기한 미설정", body)         # 임의 기한을 만들지 않음
        self.assertNotIn("3영업일 이내", body)
        self.assertNotIn("5영업일 이내", body)

    def test_guide_lists_only_launch_products_no_arbitrary_deadlines(self):
        # 주문이 없을 때(안내만 있는 페이지): 보류 상품명이 기한과 함께 등장하지 않는다.
        body = self._body()
        self.assertNotIn("연간 VIP", body)
        self.assertNotIn("전문가 보고서", body)

    def test_guide_has_no_customer_data_or_order_id(self):
        body = self._body()  # 주문 없음 → 페이지에 고객정보·주문번호가 없어야 한다
        self.assertNotIn("@", body)
        self.assertNotIn("ord_", body)


# ---- 8. 고객 이메일 표시·복사(주문 상세) ----
class RecipientEmailCopyTest(_Base):
    def _paid_with_email(self, email, pk):
        o = payments.create_order(self.store, "BASIC", email, {"birth_date": "1990-05-15"})
        payments.approve_payment(self.store, pk, o["orderId"], o["amount"], confirm_fn=_toss_ok)
        return o["orderId"]

    def _detail(self, oid):
        r = self.c.get("/admin/orders/%s" % oid, headers=_auth())
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    def test_detail_shows_recipient_email_and_copy_button(self):
        oid = self._paid_with_email("alice@example.test", "pk_a")
        body = self._detail(oid)
        self.assertIn('id="recipient-email">alice@example.test<', body)  # 선명한 전체 주소
        self.assertIn("고객 이메일 복사", body)
        self.assertIn('id="copy-email-status"', body)
        self.assertIn("받는 사람 주소와 실제 발송 여부를 확인한 뒤 누르세요", body)

    def test_copy_reads_email_element_only(self):
        oid = self._paid_with_email("alice@example.test", "pk_a")
        body = self._detail(oid)
        # 복사 대상은 recipient-email 요소(이메일 주소만). 주문번호/결제키는 복사 소스가 아니다.
        self.assertIn('getElementById("recipient-email")', body)
        self.assertNotIn("mailto:", body)  # 메일 작성/발송 안 함

    def test_list_keeps_email_masked_not_full(self):
        self._paid_with_email("alice@example.test", "pk_a")
        body = self.c.get("/admin/orders", headers=_auth()).get_data(as_text=True)
        self.assertIn("***", body)                      # 마스킹 유지
        self.assertNotIn("alice@example.test", body)    # 전체 주소는 목록에 없음

    def test_other_order_email_not_present(self):
        a = self._paid_with_email("alice@example.test", "pk_a")
        b = self._paid_with_email("bob@example.test", "pk_b")
        body_a = self._detail(a)
        body_b = self._detail(b)
        self.assertIn("alice@example.test", body_a)
        self.assertNotIn("bob@example.test", body_a)    # A 상세에 B 이메일 없음
        self.assertIn("bob@example.test", body_b)
        self.assertNotIn("alice@example.test", body_b)  # B 상세에 A 이메일 없음

    def test_detail_template_no_browser_storage_or_log(self):
        with open(os.path.join(ROOT, "templates", "admin_order_detail.html"), "r", encoding="utf-8") as f:
            tpl = f.read()
        for bad in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                    "console.log", "console.debug", "console.info"):
            self.assertNotIn(bad, tpl)

    def test_copy_button_is_not_a_form_submit(self):
        oid = self._paid_with_email("alice@example.test", "pk_a")
        body = self._detail(oid)
        self.assertIn('id="copy-email" class="btn"', body)
        self.assertIn('type="button" id="copy-email"', body)  # 폼 전송/발송 아님

    def test_detail_mobile_first_section_order(self):
        # 재배치: 발송 상태·지금 할 일 → 받는 사람 이메일+복사 → 보고서 자료 복사
        #        → 주문 정보 → 운영자 알림 상태(아래로 구분).
        oid = self._paid_with_email("alice@example.test", "pk_a")
        b = self._detail(oid)
        order = [b.index("지금 할 일"),
                 b.index("받는 사람 이메일"),
                 b.index('id="copy-email"'),
                 b.index('id="copy-report"'),
                 b.index("주문 정보"),
                 b.index("운영자 알림 상태")]
        self.assertEqual(order, sorted(order))


if __name__ == "__main__":
    unittest.main(verbosity=2)
