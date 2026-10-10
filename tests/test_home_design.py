"""새 홈 디자인(templates/index.html + static/home.css + static/app.js) 연결 회귀 테스트.

여기서 쓰는 날짜는 모두 합성(예시) 데이터다. 실제 개인정보가 아니다.
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import saju_bot
import payments


def _read(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


class HomeServedTest(unittest.TestCase):
    def setUp(self):
        self.c = saju_bot.app.test_client()

    def test_health_still_ok(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_index_and_assets_served(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        for path in ("/static/home.css", "/static/app.css", "/static/app.js"):
            self.assertEqual(self.c.get(path).status_code, 200, path)

    def test_index_uses_home_css_only(self):
        html = _read("templates/index.html")
        self.assertIn("/static/home.css", html)
        self.assertNotIn("/static/app.css", html)  # 결제·관리자 화면 공용 CSS 는 홈에서 쓰지 않는다.

    def test_pinch_zoom_allowed_for_older_users(self):
        html = _read("templates/index.html")
        self.assertIn("width=device-width", html)
        self.assertNotIn("maximum-scale", html)
        self.assertNotIn("user-scalable", html)

    def test_customer_pages_use_home_css(self):
        # 고객용 화면(홈·무료·결제 성공/실패·법적 고지)은 하나의 디자인 체계(home.css)로 통일한다.
        for rel in ("templates/payment_success.html", "templates/payment_fail.html",
                    "templates/legal.html"):
            body = _read(rel)
            self.assertIn("/static/home.css", body)
            self.assertNotIn("/static/app.css", body)

    def test_admin_pages_use_app_css(self):
        for rel in ("templates/admin_orders.html", "templates/admin_order_detail.html"):
            self.assertIn("/static/app.css", _read(rel))


class HomeContentHonestyTest(unittest.TestCase):
    def test_unbuilt_features_labeled_as_coming_soon(self):
        html = _read("templates/index.html")
        for name in ("내 노트", "궁합 초대 링크", "보고서 선물하기"):
            m = re.search(r'<span class="soon">준비 중</span><h3>' + re.escape(name) + "</h3>", html)
            self.assertIsNotNone(m, name)

    def test_no_placeholders_or_verified_claims(self):
        html = _read("templates/index.html")
        for bad in ("[입력]", "검증 완료", "정확도 100", "100% 정확", "전문가가 모두"):
            self.assertNotIn(bad, html)
        self.assertIn("전체 정확성은 독립 검증 전", html)

    def test_footer_uses_business_info_and_legal_links(self):
        html = _read("templates/index.html")
        footer = html[html.index('<footer class="foot">'):html.index("</footer>")]
        for frag in ("biz.name", "biz.pending", "/terms", "/privacy", "/refund"):
            self.assertIn(frag, footer)
        self.assertEqual(html.count("알파랩"), 0)


_BIZ_ENV = ("BIZ_NAME", "BIZ_REPRESENTATIVE", "BIZ_REG_NO", "BIZ_MAIL_ORDER_NO",
            "BIZ_ADDRESS", "BIZ_PHONE", "BIZ_EMAIL", "CANONICAL_HOST")


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _BIZ_ENV}
        for k in _BIZ_ENV:
            os.environ.pop(k, None)
        self.c = saju_bot.app.test_client()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class CanonicalRedirectTest(_EnvCase):
    APEX = {"base_url": "https://hengun.co.kr"}

    def test_no_redirect_when_env_unset(self):
        r = self.c.get("/", **self.APEX)
        self.assertEqual(r.status_code, 200)

    def test_apex_home_get_redirects_to_www(self):
        os.environ["CANONICAL_HOST"] = "www.hengun.co.kr"
        r = self.c.get("/", **self.APEX)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "https://www.hengun.co.kr/")

    def test_www_and_other_hosts_not_redirected(self):
        os.environ["CANONICAL_HOST"] = "www.hengun.co.kr"
        for base in ("https://www.hengun.co.kr", "https://saju-bot.up.railway.app", "http://localhost"):
            self.assertEqual(self.c.get("/", base_url=base).status_code, 200, base)

    def test_invalid_canonical_host_ignored(self):
        for bad in ("hengun.co.kr", "evil.com/x", "www.a.com?x=1", "www.", "http://www.a.com"):
            os.environ["CANONICAL_HOST"] = bad
            self.assertEqual(self.c.get("/", **self.APEX).status_code, 200, bad)

    def test_payment_webhook_health_api_not_redirected_from_apex(self):
        os.environ["CANONICAL_HOST"] = "www.hengun.co.kr"
        for method, path in (("get", "/health"), ("get", "/payment/success"),
                             ("get", "/payment/fail"), ("post", "/webhooks/toss"),
                             ("post", "/free-insights"), ("post", "/api/orders")):
            r = getattr(self.c, method)(path, **self.APEX)
            self.assertNotIn(r.status_code, (301, 302, 303, 307, 308), path)


class LegalPagesTest(_EnvCase):
    def test_pages_ok_with_draft_notice_and_pending(self):
        for path in ("/terms", "/privacy", "/refund"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 200, path)
            body = r.get_data(as_text=True)
            self.assertIn("입력 대기", body, path)
            self.assertIn("/static/home.css", body)
            self.assertNotIn("/static/app.css", body)

    def test_home_footer_shows_pending_and_links(self):
        body = self.c.get("/").get_data(as_text=True)
        self.assertIn("입력 대기", body)
        for link in ('href="/terms"', 'href="/privacy"', 'href="/refund"'):
            self.assertIn(link, body)
        self.assertIn("알파랩", body)

    def test_real_values_shown_and_escaped(self):
        os.environ["BIZ_REPRESENTATIVE"] = "홍길동"
        os.environ["BIZ_PHONE"] = "<script>alert(1)</script>"
        body = self.c.get("/terms").get_data(as_text=True)
        self.assertIn("홍길동", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_confirmed_reg_no_shown_and_no_invented_mail_order_no(self):
        # 사업자등록번호는 확정값(564-05-02583)으로 표기하되, 통신판매업 신고 상태·주소 등
        # 미확정 항목은 '입력 대기'로 남기고 환불 '일절 불가' 문구는 쓰지 않는다.
        body = self.c.get("/refund").get_data(as_text=True)
        self.assertIn("564-05-02583", body)
        self.assertIn("입력 대기", body)
        self.assertNotIn("무조건 환불 불가", body)
        self.assertNotIn("환불이 불가능", body)


class FreePayloadWarningsTest(unittest.TestCase):
    """/free-insights 응답이 화면에 반영할 경고·정확도 안내를 실제로 담고 있는지."""

    def setUp(self):
        self.c = saju_bot.app.test_client()

    def _post(self, **over):
        body = {"calendar": "solar", "birth_date": "1990-01-01", "birth_time": "14:30",
                "gender": "남", "is_leap_month": False,
                "birth_place": {"country": "KR", "city": "서울", "longitude": 126.978},
                "alias": "별칭", "topics": ["애정"]}
        body.update(over)
        return self.c.post("/free-insights", json=body)

    def test_jieqi_boundary_note_near_ipchun(self):
        # 2012-02-04 19:23 서울 = 입춘(18:22:24 CST) 직후 → 절입 경계 경고가 무료 결과 notes 에 표시.
        j = self._post(birth_date="2012-02-04", birth_time="19:23").get_json()
        self.assertTrue(any("절입" in n for n in j["insight"]["notes"]))

    def test_no_jieqi_note_away_from_boundary(self):
        j = self._post(birth_date="2012-02-04", birth_time="12:00").get_json()
        self.assertFalse(any("절입" in n for n in j["insight"]["notes"]))

    def test_accuracy_note_present_and_not_claiming_verified(self):
        r = self._post()
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        note = j["saju"]["accuracy_note"]
        self.assertIn("독립 기준 교차검증 전까지 확정되지 않았습니다", note)
        self.assertEqual(j["insight"]["verification"], "NOT_VERIFIED")

    def test_unknown_time_warning(self):
        j = self._post(birth_time=None).get_json()
        self.assertIsNone(j["saju"]["pillars"]["time"])
        self.assertTrue(any("시주" in n and "계산하지 않았" in n for n in j["insight"]["notes"]))

    def test_late_night_boundary_warning(self):
        # 서울 경도로 진태양시 보정하면 23:30 은 22시대로 내려가므로, 보정 없는(지역 모름) 입력으로 검사한다.
        j = self._post(birth_time="23:30", birth_place={"country": "KR", "city": ""}).get_json()
        self.assertTrue(j["saju"]["boundary_warning"])
        self.assertTrue(any("밤 11시대" in n for n in j["insight"]["notes"]))

    def test_unknown_region_does_not_send_longitude(self):
        body = {"calendar": "solar", "birth_date": "1990-01-01", "birth_time": "14:30", "gender": "남",
                "birth_place": {"country": "KR", "city": ""}}
        j = self.c.post("/free-insights", json=body).get_json()
        self.assertTrue(j["saju"]["needs_confirmation"])
        self.assertFalse(j["saju"]["time_correction"]["applied"])

    def test_free_insights_does_not_create_orders(self):
        before = len(getattr(saju_bot.ORDER_STORE, "_orders", {}) or {})
        self._post()
        after = len(getattr(saju_bot.ORDER_STORE, "_orders", {}) or {})
        self.assertEqual(before, after)


class ClientCodeSafetyTest(unittest.TestCase):
    def test_no_browser_storage_or_url_leak(self):
        blob = _read("static/app.js") + _read("templates/index.html")
        for bad in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                    "history.pushState", "history.replaceState", "location.search", "location.hash =",
                    "?birth", "?alias", "?email"):
            self.assertNotIn(bad, blob, bad)

    def test_no_console_log_of_customer_data(self):
        js = _read("static/app.js")
        self.assertNotIn("console.log", js)
        self.assertNotIn("console.debug", js)
        self.assertNotIn("console.info", js)

    def test_server_values_rendered_as_text(self):
        js = _read("static/app.js")
        a = js.index("function renderMyeongsikInto")
        b = js.index("// ---- 무료 사주 요약 화면 ----")
        self.assertNotIn("innerHTML", js[a:b])
        a = js.index("function row(label, value)")
        b = js.index("function orderPayload")
        self.assertNotIn("innerHTML = html", js[a:b])
        self.assertIn("accuracy_note", js)

    def test_notes_rendered_right_after_myeongsik(self):
        js = _read("static/app.js")
        i = js.index("box.appendChild(ms);")
        self.assertIn("ins.notes", js[i:i + 200])


class PriceCanonicalTest(unittest.TestCase):
    """화면 가격 표시가 서버 정본(payments.PRODUCTS)과 어긋나지 않는지."""

    def test_displayed_prices_match_server(self):
        js = _read("static/app.js")
        code_map = dict(re.findall(r'(\w+_\d+):\s*"([A-Z_]+)"', js[js.index("var PRODUCT_CODE"):]))
        shown = {}
        for pid, price in re.findall(r'id:\s*"(\w+)",\s*name:[^}]*?price:\s*"([\d,]+)원"', js):
            shown[pid] = int(price.replace(",", ""))
        self.assertEqual(len(code_map), 6)
        for pid, code in code_map.items():
            self.assertIn(pid, shown, pid)
            self.assertEqual(shown[pid], payments.PRODUCTS[code]["amount"], pid)

    def test_order_amount_not_sent_from_client(self):
        js = _read("static/app.js")
        a = js.index("function orderPayload")
        b = js.index("function logPayError")
        self.assertNotIn("amount", js[a:b])


if __name__ == "__main__":
    unittest.main()
