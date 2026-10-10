"""saju_engine / saju_bot 단위 및 회귀 테스트 (표준 unittest, 추가 의존성 없음).

주의: 여기서 쓰는 날짜는 모두 합성(예시) 데이터다. 실제 개인정보가 아니다.
이 테스트는 '라이브러리가 결과를 반환한다'는 것을 검증하며,
'한국 만세력 정확성'을 판정하지 않는다(신뢰 기준값 미확보).
"""

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import saju_engine
from saju_engine import compute_saju, SajuInputError
import saju_bot
import saju_insights
import payments
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode("ascii")
_PAY_ENV_KEYS = ["PAYMENTS_ENABLED", "PAYMENT_MODE", "TOSS_CLIENT_KEY",
                 "TOSS_SECRET_KEY", "ORDER_ENCRYPTION_KEY", "DATABASE_URL"]


def _enable_pay(mode="test"):
    os.environ["PAYMENTS_ENABLED"] = "true"
    os.environ["PAYMENT_MODE"] = mode
    os.environ["TOSS_CLIENT_KEY"] = mode + "_ck_sampleclient"
    os.environ["TOSS_SECRET_KEY"] = mode + "_sk_samplesecret"
    os.environ["ORDER_ENCRYPTION_KEY"] = _FERNET_KEY


def _clear_pay():
    for k in _PAY_ENV_KEYS:
        os.environ.pop(k, None)


def _toss_ok(**kw):
    return {"orderId": kw["order_id"], "totalAmount": kw["amount"], "status": "DONE"}


def _is_pillar(p):
    return (
        isinstance(p, dict)
        and len(p["ganzhi"]) == 2
        and p["gan"] and p["zhi"]
        and p["gan_ko"] and p["zhi_ko"]
    )


class EngineCalcTest(unittest.TestCase):
    def test_solar_basic(self):
        r = compute_saju("solar", "1990-05-15", "08:30", "남")
        self.assertEqual(r["status"], "ok")
        for k in ("year", "month", "day", "time"):
            self.assertTrue(_is_pillar(r["pillars"][k]), k)
        self.assertEqual(r["sect"], 2)
        self.assertFalse(r["convention"]["longitude_correction"])
        self.assertIn("message", r)  # 기존 message 키 유지

    def test_lunar_basic(self):
        r = compute_saju("lunar", "1990-04-21", "08:30", "여", is_leap_month=False)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(_is_pillar(r["pillars"]["day"]))
        self.assertIn("solar", r)
        self.assertIn("lunar", r)

    def test_real_leap_roundtrip(self):
        # 합성 예시: 2020년 윤4월 15일(실존 윤달)
        r = compute_saju("lunar", "2020-04-15", None, "남", is_leap_month=True)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["lunar"]["is_leap_month"])
        self.assertEqual(r["lunar"]["month"], 4)
        self.assertEqual(r["lunar"]["year"], 2020)
        # 양력 변환 결과가 존재해야 한다
        self.assertTrue(1 <= r["solar"]["month"] <= 12)

    def test_nonexistent_leap_rejected(self):
        # 1990년에는 윤4월이 없다 -> 입력 오류(400 상당)
        with self.assertRaises(SajuInputError):
            compute_saju("lunar", "1990-04-15", None, "남", is_leap_month=True)

    def test_time_missing_timepillar_null(self):
        r = compute_saju("solar", "1990-05-15", None, "남")
        self.assertIsNone(r["pillars"]["time"])
        self.assertIn("미상", r["message"])

    def test_late_zi_2300_boundary(self):
        r = compute_saju("solar", "1990-05-15", "23:00", "남")
        b = r["boundary_warning"]
        self.assertIsNotNone(b)
        self.assertEqual(b["type"], "late_zi_23h")
        self.assertIn("sect1", b)
        self.assertIn("sect2", b)
        # 기본 pillars 는 sect=2 결과와 일치
        self.assertEqual(r["pillars"]["day"]["ganzhi"], b["sect2"]["day"]["ganzhi"])

    def test_late_zi_2330_sects_differ(self):
        r = compute_saju("solar", "1990-05-15", "23:30", "남")
        b = r["boundary_warning"]
        self.assertIsNotNone(b)
        # sect=1 과 sect=2 의 일주는 달라야 한다(23시대 특성)
        self.assertNotEqual(b["sect1"]["day"]["ganzhi"], b["sect2"]["day"]["ganzhi"])

    def test_non_boundary_no_warning(self):
        r = compute_saju("solar", "1990-05-15", "08:30", "남")
        self.assertIsNone(r["boundary_warning"])


class EngineValidationTest(unittest.TestCase):
    def test_bad_calendar(self):
        with self.assertRaises(SajuInputError):
            compute_saju("julian", "1990-05-15", "08:30", "남")

    def test_bad_date_format(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1990/05/15", "08:30", "남")

    def test_nonexistent_solar_date(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1990-02-30", "08:30", "남")

    def test_year_out_of_range(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1800-05-15", "08:30", "남")

    def test_bad_time_format(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1990-05-15", "8:30", "남")

    def test_bad_time_range(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1990-05-15", "24:00", "남")

    def test_bad_gender(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1990-05-15", "08:30", "x")

    def test_solar_with_leap_rejected(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1990-05-15", "08:30", "남", is_leap_month=True)

    def test_non_bool_leap_rejected(self):
        with self.assertRaises(SajuInputError):
            compute_saju("lunar", "1990-04-21", "08:30", "남", is_leap_month="true")


class FlaskRegressionTest(unittest.TestCase):
    def setUp(self):
        self.c = saju_bot.app.test_client()

    def test_health_unchanged(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_index_200(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        # 다단계 UI(templates/index.html). /saju 호출은 static/app.js로 이동.
        for token in ["생년월일", "태어난 시간", "성별", "양력", "음력", "윤달"]:
            self.assertIn(token, body)

    def test_saju_ok(self):
        r = self.c.post("/saju", json={"calendar": "solar", "birth_date": "1990-05-15",
                                        "birth_time": "08:30", "gender": "남"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")

    def test_saju_bad_input_400(self):
        for payload in [
            {"calendar": "solar", "birth_date": "bad", "gender": "남"},
            {"calendar": "solar", "birth_date": "1990-05-15", "gender": "x"},
            {"calendar": "solar", "birth_date": "1990-05-15", "gender": "남", "is_leap_month": True},
            {"calendar": "lunar", "birth_date": "1990-04-15", "gender": "남", "is_leap_month": True},
        ]:
            r = self.c.post("/saju", json=payload)
            self.assertEqual(r.status_code, 400, payload)
            self.assertEqual(r.get_json()["status"], "error")


class SecurityStaticScanTest(unittest.TestCase):
    """AST 기반 정적 검사. 주석/docstring 안의 단어가 아니라 실제 import/호출만 본다."""

    FILES = ["saju_engine.py", "saju_bot.py", "saju_time.py"]
    FORBIDDEN_IMPORTS = {"subprocess", "pickle", "socket", "requests", "urllib",
                         "http", "ctypes", "anthropic", "openai", "httpx", "aiohttp"}
    FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
    FORBIDDEN_ATTR_CALLS = {"os.system", "os.popen", "os.remove", "os.unlink"}

    def _analyze(self, fn):
        with open(os.path.join(ROOT, fn), "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports, names, attrs, env_keys, opens = set(), set(), set(), [], 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                fo = node.func
                if isinstance(fo, ast.Name):
                    names.add(fo.id)
                    if fo.id == "open":
                        opens += 1
                elif isinstance(fo, ast.Attribute):
                    parts = []
                    cur = fo
                    while isinstance(cur, ast.Attribute):
                        parts.append(cur.attr)
                        cur = cur.value
                    if isinstance(cur, ast.Name):
                        parts.append(cur.id)
                    dotted = ".".join(reversed(parts))
                    attrs.add(dotted)
                    # os.environ.get(KEY) / os.getenv(KEY) 의 키를 수집
                    if dotted in ("os.environ.get", "os.getenv") and node.args:
                        a0 = node.args[0]
                        env_keys.append(a0.value if isinstance(a0, ast.Constant) else "<non-literal>")
        return imports, names, attrs, env_keys, opens

    def test_no_dangerous_imports_or_calls(self):
        for fn in self.FILES:
            imports, names, attrs, _, _ = self._analyze(fn)
            self.assertFalse(imports & self.FORBIDDEN_IMPORTS,
                             "%s imports %s" % (fn, imports & self.FORBIDDEN_IMPORTS))
            self.assertFalse(names & self.FORBIDDEN_CALLS,
                             "%s calls %s" % (fn, names & self.FORBIDDEN_CALLS))
            bad_attr = {a for a in attrs if a in self.FORBIDDEN_ATTR_CALLS or a.startswith("subprocess.")}
            self.assertFalse(bad_attr, "%s calls %s" % (fn, bad_attr))

    def test_engine_no_env_or_file_access(self):
        # saju_engine 은 환경변수 접근도, open()(파일 I/O)도 하지 않아야 한다.
        _, _, attrs, env_keys, opens = self._analyze("saju_engine.py")
        self.assertEqual(env_keys, [], "engine accesses env vars")
        self.assertNotIn("os.environ", {a.rsplit(".", 1)[0] for a in attrs})
        self.assertEqual(opens, 0, "engine must not call open()")

    def test_bot_env_access_is_only_PORT(self):
        # saju_bot 의 유일하게 허용되는 환경변수 접근은 PORT 바인딩뿐이다(비밀정보 접근 없음).
        _, _, _, env_keys, _ = self._analyze("saju_bot.py")
        self.assertTrue(all(k == "PORT" for k in env_keys),
                        "saju_bot accesses non-PORT env vars: %s" % env_keys)

    def test_no_ai_modules_loaded(self):
        self.assertNotIn("anthropic", sys.modules)
        self.assertNotIn("openai", sys.modules)


class PrivacyNoPersistenceTest(unittest.TestCase):
    def _snapshot(self):
        names = set()
        for base in (ROOT, os.path.join(ROOT, "tests")):
            for n in os.listdir(base):
                if n == "__pycache__":
                    continue
                names.add(os.path.join(base, n))
        return names

    def test_compute_creates_no_files(self):
        before = self._snapshot()
        compute_saju("solar", "1990-05-15", "08:30", "남")
        compute_saju("lunar", "2020-04-15", "23:30", "여", is_leap_month=True)
        after = self._snapshot()
        self.assertEqual(before, after, "compute_saju must not create files")


class KoreanLunarFixTest(unittest.TestCase):
    """Phase 2A: 한국천문연구원(KASI) 기준 음력 변환 교정 회귀."""

    def test_case2_korean_lunar_fixed(self):
        # 한국 음력 1987-05-10 평달 14:33 -> 양력 1987-06-06, 일주 丙戌
        r = compute_saju("lunar", "1987-05-10", "14:33", "남", is_leap_month=False)
        self.assertEqual((r["solar"]["year"], r["solar"]["month"], r["solar"]["day"]), (1987, 6, 6))
        self.assertEqual(r["pillars"]["day"]["ganzhi"], "丙戌")
        # 기존 중국식 오류값(1987-06-05 / 乙酉)이 더 이상 나오지 않아야 한다
        self.assertNotEqual((r["solar"]["year"], r["solar"]["month"], r["solar"]["day"]), (1987, 6, 5))
        self.assertNotEqual(r["pillars"]["day"]["ganzhi"], "乙酉")

    def test_solar_1987_06_06_to_korean_lunar(self):
        r = compute_saju("solar", "1987-06-06", "14:33", "남")
        self.assertEqual((r["lunar"]["year"], r["lunar"]["month"], r["lunar"]["day"]), (1987, 5, 10))
        self.assertFalse(r["lunar"]["is_leap_month"])

    def test_leap_2020_to_solar(self):
        r = compute_saju("lunar", "2020-04-15", None, "남", is_leap_month=True)
        self.assertEqual((r["solar"]["year"], r["solar"]["month"], r["solar"]["day"]), (2020, 6, 6))

    def test_solar_2020_06_06_reverse_is_leap4(self):
        r = compute_saju("solar", "2020-06-06", None, "남")
        self.assertEqual((r["lunar"]["year"], r["lunar"]["month"], r["lunar"]["day"]), (2020, 4, 15))
        self.assertTrue(r["lunar"]["is_leap_month"])

    def test_lunar_source_is_kasi(self):
        r = compute_saju("solar", "1990-05-15", "08:30", "남")
        self.assertEqual(r["lunar"]["source"], "korean_lunar_calendar(KASI)")

    def test_future_date_rejected(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "2099-01-01", "08:30", "남")

    def test_before_1900_rejected(self):
        with self.assertRaises(SajuInputError):
            compute_saju("solar", "1899-12-31", "08:30", "남")


class TrueSolarCorrectionTest(unittest.TestCase):
    """Phase 2B: 진태양시(경도+역사 표준시/DST+균시차) 보정 회귀.

    주의: 기준값은 과제 제공값이며, 계산 결과에 맞춰 바꾸지 않는다.
    CASE2 월주는 1987 서머타임 적용 여부로 기준 간 불일치가 있어 단정하지 않는다.
    """

    def _gz(self, r):
        p = r["pillars"]
        return (p["year"]["ganzhi"], p["month"]["ganzhi"], p["day"]["ganzhi"],
                p["time"]["ganzhi"] if p["time"] else None)

    def test_case1_full(self):
        r = compute_saju("solar", "2012-02-04", "19:23", "남", longitude=126.978)
        self.assertEqual(self._gz(r), ("壬辰", "壬寅", "乙未", "乙酉"))
        self.assertTrue(r["time_correction"]["applied"])
        self.assertEqual(r["time_correction"]["standard_meridian"], 135.0)

    def test_case2_year_day_hour(self):
        # 월주는 서머타임 처리 차이로 기준 불일치 -> 단정하지 않음
        r = compute_saju("lunar", "1987-05-10", "14:33", "남", is_leap_month=False, longitude=128.5918)
        p = r["pillars"]
        self.assertEqual(p["year"]["ganzhi"], "丁卯")
        self.assertEqual(p["day"]["ganzhi"], "丙戌")
        self.assertEqual(p["time"]["ganzhi"], "乙未")
        self.assertEqual(r["time_correction"]["dst_minutes"], 60.0)  # 1987 서머타임 적용

    def test_case3_hour_and_historical_stdtime(self):
        r = compute_saju("solar", "1958-08-08", "09:52", "남", longitude=126.978)
        self.assertEqual(r["pillars"]["time"]["ganzhi"], "甲辰")  # 과제 요구 후보
        self.assertEqual(r["pillars"]["day"]["ganzhi"], "丁巳")
        # 1958: UTC+8:30 표준시(127.5°E) + 당시 서머타임
        self.assertEqual(r["time_correction"]["standard_meridian"], 127.5)
        self.assertEqual(r["time_correction"]["dst_minutes"], 60.0)

    def test_case4_full_and_date_rollover(self):
        r = compute_saju("lunar", "1976-12-20", "00:38", "남", is_leap_month=False, longitude=127.148)
        self.assertEqual(self._gz(r), ("丁巳", "壬寅", "甲午", "丙子"))
        # 보정 후 전날(1977-02-06)로 날짜 이동
        self.assertTrue(r["time_correction"]["true_solar_local"].startswith("1977-02-06"))
        self.assertIsNotNone(r["boundary_warning"])  # 보정 후 23시대

    def test_case5_full(self):
        r = compute_saju("solar", "1994-01-17", "15:41", "남", longitude=126.978)
        self.assertEqual(self._gz(r), ("癸酉", "乙丑", "癸卯", "己未"))

    def test_no_region_wall_clock_with_warning(self):
        r = compute_saju("solar", "2012-02-04", "19:23", "남")
        self.assertFalse(r["time_correction"]["applied"])
        self.assertTrue(r["needs_confirmation"])

    def test_no_time_no_correction(self):
        r = compute_saju("solar", "2012-02-04", None, "남", longitude=126.978)
        self.assertIsNone(r["pillars"]["time"])
        self.assertFalse(r["time_correction"]["applied"])
        self.assertFalse(r["needs_confirmation"])

    def test_longitude_bounds_ok(self):
        for lon in (124.0, 132.0):
            r = compute_saju("solar", "2000-01-01", "12:00", "남", longitude=lon)
            self.assertTrue(r["time_correction"]["applied"])

    def test_longitude_out_of_range(self):
        for lon in (123.9, 132.1):
            with self.assertRaises(SajuInputError):
                compute_saju("solar", "2000-01-01", "12:00", "남", longitude=lon)

    def test_modern_kst_meridian(self):
        r = compute_saju("solar", "2000-06-01", "12:00", "남", longitude=126.978)
        self.assertEqual(r["time_correction"]["standard_meridian"], 135.0)
        self.assertEqual(r["time_correction"]["dst_minutes"], 0.0)

    def test_1987_vs_1986_dst(self):
        on = compute_saju("solar", "1987-07-01", "12:00", "남", longitude=126.978)
        off = compute_saju("solar", "1986-07-01", "12:00", "남", longitude=126.978)
        self.assertEqual(on["time_correction"]["dst_minutes"], 60.0)
        self.assertEqual(off["time_correction"]["dst_minutes"], 0.0)


class Phase2BFlaskTest(unittest.TestCase):
    def setUp(self):
        self.c = saju_bot.app.test_client()

    def test_health_unchanged(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_saju_with_birth_place_ok(self):
        r = self.c.post("/saju", json={
            "calendar": "solar", "birth_date": "2012-02-04", "birth_time": "19:23",
            "gender": "남", "birth_place": {"country": "KR", "city": "서울", "longitude": 126.978}})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["time_correction"]["applied"])

    def test_foreign_country_400(self):
        r = self.c.post("/saju", json={
            "calendar": "solar", "birth_date": "2012-02-04", "birth_time": "19:23",
            "gender": "남", "birth_place": {"country": "JP", "longitude": 139.7}})
        self.assertEqual(r.status_code, 400)

    def test_bad_longitude_400(self):
        r = self.c.post("/saju", json={
            "calendar": "solar", "birth_date": "2012-02-04", "birth_time": "19:23",
            "gender": "남", "birth_place": {"country": "KR", "longitude": 150.0}})
        self.assertEqual(r.status_code, 400)

    def test_no_birth_place_still_ok(self):
        r = self.c.post("/saju", json={
            "calendar": "solar", "birth_date": "2012-02-04", "birth_time": "19:23", "gender": "남"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["needs_confirmation"])


class CustomerUITest(unittest.TestCase):
    """Batch 2: 고객 입력 UI + 상품 + 보고서 자료."""

    def setUp(self):
        self.c = saju_bot.app.test_client()

    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_index_served(self):
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_static_assets_served(self):
        self.assertEqual(self.c.get("/static/app.css").status_code, 200)
        self.assertEqual(self.c.get("/static/app.js").status_code, 200)

    def test_step_markup_present(self):
        html = self._read("templates/index.html")
        for n in range(1, 9):
            self.assertIn('data-step="%d"' % n, html)
        self.assertIn('id="prev"', html)
        self.assertIn('id="next"', html)
        self.assertIn('id="progress-bar"', html)

    def test_calendar_leap_and_gender(self):
        html = self._read("templates/index.html")
        for t in ["양력", "음력", "윤달", "성별", 'name="calendar"', 'name="is_leap_month"']:
            self.assertIn(t, html)

    def test_time_status_branches(self):
        html = self._read("templates/index.html")
        for v in ['value="exact"', 'value="approx"', 'value="unknown"']:
            self.assertIn(v, html)

    def test_region_cities_and_longitude(self):
        js = self._read("static/app.js")
        self.assertIn("서울", js)
        self.assertIn("126.978", js)
        self.assertIn("속초", js)       # CASE2 도시
        self.assertIn("lon: null", js)  # 지역 모름 허용
        self.assertIn("birth_place", js)
        self.assertIn("longitude", js)

    def test_region_unknown_warning_present(self):
        html = self._read("templates/index.html")
        self.assertIn("진태양시 보정이 적용되지 않", html)

    def test_seven_products_and_prices(self):
        js = self._read("static/app.js")
        for price in ["0원", "9,900원", "39,000원", "99,000원", "290,000원", "590,000원", "990,000원"]:
            self.assertIn(price, js)
        # 7개 상품 id
        for pid in ["free", "basic_9900", "deep_39000", "expert_99000",
                    "life_290000", "relation_590000", "vip_990000"]:
            self.assertIn(pid, js)

    def test_no_phone_video_inperson_offering(self):
        html = self._read("templates/index.html")
        self.assertIn("전화·화상·대면 상담은 제공하지 않습니다", html)

    def test_paid_products_labeled_as_human_reviewed_not_auto(self):
        # 실제 제공 방식(담당자 확인 후 이메일, 자동/즉시 아님)과 광고 문구를 일치시킨다.
        # 과거의 'AI 기반 자동 해석'·'전문가 검토' 분류는 제공하지 못하는 약속이라 제거했다.
        js = self._read("static/app.js")
        self.assertIn("담당자 확인 후 이메일", js)
        self.assertNotIn("AI 기반 자동 해석", js)
        self.assertNotIn("전문가 검토", js)
        self.assertNotIn("화면(자동)", js)

    def test_payment_disabled(self):
        js = self._read("static/app.js")
        self.assertIn("결제 기능 준비 중", js)
        self.assertIn("disabled", js)

    def test_js_uses_escaping(self):
        js = self._read("static/app.js")
        self.assertIn("function esc(", js)
        self.assertIn("textContent", js)

    # ---- /report-data 엔드포인트 ----
    def _report(self, **over):
        payload = {
            "calendar": "solar", "birth_date": "1990-05-15", "birth_time": "08:30",
            "gender": "남", "consultation_type": "종합",
            "birth_place": {"country": "KR", "city": "서울", "longitude": 126.978},
            "alias": "바다", "question": "올해 이직?", "topics": ["직업", "재물"],
            "selected_product": "expert_99000",
        }
        payload.update(over)
        return self.c.post("/report-data", json=payload)

    def test_report_data_ok(self):
        r = self._report()
        self.assertEqual(r.status_code, 200)
        rep = r.get_json()["report"]
        self.assertEqual(rep["schema"], "saju_report_request_v1")
        self.assertIn("customer_data", rep)
        self.assertIn("saju", rep)
        self.assertEqual(rep["customer_data"]["consultation_type"], "종합")
        self.assertEqual(rep["selected_product"], "expert_99000")

    def test_report_data_jieqi_boundary_warning(self):
        rep = self._report(birth_date="2012-02-04", birth_time="19:23").get_json()["report"]
        self.assertTrue(any("절입" in w for w in rep["warnings"]))
        self.assertIsNotNone(rep["saju"]["jieqi_boundary"])

    def test_report_data_no_jieqi_away_from_boundary(self):
        rep = self._report(birth_date="1990-05-15", birth_time="08:30").get_json()["report"]
        self.assertFalse(any("절입" in w for w in rep["warnings"]))
        self.assertIsNone(rep["saju"]["jieqi_boundary"])

    def test_report_excludes_pii(self):
        r = self._report(email="x@y.com", phone="010-1234-5678",
                         payment="4111111111111111", address="서울시 강남구 ...")
        import json as _json
        blob = _json.dumps(r.get_json(), ensure_ascii=False)
        for forbidden in ["x@y.com", "010-1234-5678", "4111111111111111", "강남구"]:
            self.assertNotIn(forbidden, blob)

    def test_report_sanitizes_control_chars_and_length(self):
        r = self._report(alias="바\x00다\x07" + "가" * 100, question="줄\x01바꿈")
        rep = r.get_json()["report"]
        alias = rep["customer_data"]["alias"]
        self.assertNotIn("\x00", alias)
        self.assertNotIn("\x07", alias)
        self.assertLessEqual(len(alias), 50)
        self.assertNotIn("\x01", rep["customer_data"]["question"])

    def test_report_bad_consultation_type_400(self):
        r = self._report(consultation_type="운세")
        self.assertEqual(r.status_code, 400)

    def test_report_unknown_product_400(self):
        r = self._report(selected_product="gold_999")
        self.assertEqual(r.status_code, 400)

    def test_report_invalid_birthdate_400(self):
        r = self._report(birth_date="bad-date")
        self.assertEqual(r.status_code, 400)


class EmailAndConfirmTest(unittest.TestCase):
    """UI 수정: 유료 이메일 입력 + 8단계 고객 확인 화면(내부 JSON 제거)."""

    def setUp(self):
        self.c = saju_bot.app.test_client()

    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_email_field_present_and_hidden_by_default(self):
        html = self._read("templates/index.html")
        self.assertIn('id="email"', html)
        self.assertIn('maxlength="254"', html)
        self.assertRegex(html, r'id="paid-extra"[^>]*hidden')  # 기본 숨김(무료)

    def test_email_logic_in_js(self):
        js = self._read("static/app.js")
        for t in ["EMAIL_RE", "validEmail", "maskEmail", "254", "trim(", "syncPaidExtra", "isPaid"]:
            self.assertIn(t, js)
        self.assertIn('selectedProduct = "free"', js)  # 기본 무료 -> 이메일 미수집

    def test_step8_title_and_no_internal_json(self):
        html = self._read("templates/index.html")
        self.assertIn("신청 내용 확인", html)
        for forbidden in ["report-json", "명리학 보고서용 자료 복사", "customer_data"]:
            self.assertNotIn(forbidden, html)
        js = self._read("static/app.js")
        for forbidden in ["report-json", "/report-data", "copy-report", "customer_data"]:
            self.assertNotIn(forbidden, js)

    def test_step8_customer_fields_in_js(self):
        js = self._read("static/app.js")
        for t in ["선택 상품", "예상 발송 기간", "수령 이메일", "maskEmail(", "무료 명식"]:
            self.assertIn(t, js)

    def test_no_fake_order_or_completion_language(self):
        blob = self._read("static/app.js") + self._read("templates/index.html")
        self.assertIn("아직 주문이 접수되지 않습니다", blob)
        for bad in ["주문번호", "접수 완료", "결제 완료", "발송 완료"]:
            self.assertNotIn(bad, blob)

    def test_free_complete_button_removed(self):
        # 무료 결과 화면 개편: 중복 '무료 명식 확인 완료' 버튼/문구 제거.
        js = self._read("static/app.js")
        self.assertNotIn("무료 명식 확인 완료", js)
        self.assertNotIn('id="free-done"', js)

    def test_report_data_excludes_email(self):
        r = self.c.post("/report-data", json={
            "calendar": "solar", "birth_date": "1990-05-15", "gender": "남",
            "consultation_type": "종합", "selected_product": "expert_99000",
            "email": "user@example.com"})
        self.assertEqual(r.status_code, 200)
        import json as _json
        blob = _json.dumps(r.get_json(), ensure_ascii=False)
        self.assertNotIn("user@example.com", blob)
        self.assertNotIn("email", r.get_json()["report"]["customer_data"])

    def test_payment_still_disabled(self):
        js = self._read("static/app.js")
        self.assertIn("결제 기능 준비 중", js)
        self.assertIn("disabled", js)


class PaymentCoreTest(unittest.TestCase):
    """payments.py 단위 보안 검증(네트워크 mock, In-Memory 저장소)."""

    def setUp(self):
        _enable_pay("test")
        self.store = payments.InMemoryOrderStore()

    def tearDown(self):
        _clear_pay()

    def test_server_price_table(self):
        self.assertEqual(payments.PRODUCTS["BASIC"]["amount"], 9900)
        self.assertEqual(payments.PRODUCTS["DEEP"]["amount"], 39000)
        self.assertEqual(payments.PRODUCTS["EXPERT"]["amount"], 99000)
        self.assertEqual(payments.PRODUCTS["LIFE_DESIGN"]["amount"], 290000)
        self.assertEqual(payments.PRODUCTS["RELATION_BUSINESS"]["amount"], 590000)
        self.assertEqual(payments.PRODUCTS["ANNUAL_VIP"]["amount"], 990000)
        self.assertNotIn("FREE", payments.PRODUCTS)

    def test_create_order_uses_server_price(self):
        r = payments.create_order(self.store, "BASIC", "a@b.com", {"q": "x"})
        self.assertEqual(r["amount"], 9900)  # 클라이언트 금액과 무관
        self.assertEqual(r["orderName"], "기본 해석")
        self.assertTrue(r["orderId"].startswith("ord_"))

    def test_free_and_unknown_product_rejected(self):
        with self.assertRaises(payments.OrderValidationError):
            payments.create_order(self.store, "FREE", "a@b.com", {})
        with self.assertRaises(payments.OrderValidationError):
            payments.create_order(self.store, "GOLD", "a@b.com", {})

    def test_invalid_email_rejected(self):
        with self.assertRaises(payments.OrderValidationError):
            payments.create_order(self.store, "BASIC", "not-an-email", {})

    def test_order_id_unpredictable_unique(self):
        ids = set(payments.generate_order_id() for _ in range(2000))
        self.assertEqual(len(ids), 2000)
        self.assertGreaterEqual(len(payments.generate_order_id()), 20)

    def test_private_data_encrypted_no_plaintext(self):
        payments.create_order(self.store, "EXPERT", "secret@user.com",
                              {"question": "민감한질문", "birth_date": "1990-05-15"})
        for rec in self.store.private.values():
            blob = rec["encrypted_email"] + rec["encrypted_consultation_payload"]
            self.assertNotIn("secret@user.com", blob)
            self.assertNotIn("민감한질문", blob)
            self.assertNotIn("1990-05-15", blob)
        # 복호화하면 원복
        pv = list(self.store.private.values())[0]
        self.assertEqual(payments.decrypt(pv["encrypted_email"]), "secret@user.com")

    def test_disabled_when_env_missing(self):
        _clear_pay()
        self.assertFalse(payments.payments_enabled())
        with self.assertRaises(payments.PaymentConfigError):
            payments.create_order(self.store, "BASIC", "a@b.com", {})

    def test_mode_key_mix_blocked(self):
        _enable_pay("test")
        os.environ["TOSS_SECRET_KEY"] = "live_sk_wrongmode"  # 모드 혼용
        self.assertFalse(payments.payments_enabled())

    def test_approve_success(self):
        o = payments.create_order(self.store, "BASIC", "a@b.com", {})
        res = payments.approve_payment(self.store, "pk_1", o["orderId"], 9900, confirm_fn=_toss_ok)
        self.assertEqual(res["status"], "PAID")
        self.assertEqual(self.store.get_order(o["orderId"])["status"], "PAID")

    def test_approve_amount_mismatch_rejected(self):
        o = payments.create_order(self.store, "BASIC", "a@b.com", {})
        with self.assertRaises(payments.OrderValidationError):
            payments.approve_payment(self.store, "pk_1", o["orderId"], 100, confirm_fn=_toss_ok)
        self.assertNotEqual(self.store.get_order(o["orderId"])["status"], "PAID")

    def test_approve_unknown_order_rejected(self):
        with self.assertRaises(payments.OrderValidationError):
            payments.approve_payment(self.store, "pk", "ord_doesnotexist", 9900, confirm_fn=_toss_ok)

    def test_approve_idempotent(self):
        o = payments.create_order(self.store, "DEEP", "a@b.com", {})
        payments.approve_payment(self.store, "pk_X", o["orderId"], 39000, confirm_fn=_toss_ok)

        def _boom(**kw):
            raise AssertionError("toss must not be called again")
        res2 = payments.approve_payment(self.store, "pk_X", o["orderId"], 39000, confirm_fn=_boom)
        self.assertTrue(res2.get("idempotent"))

    def test_approve_toss_failure_not_paid(self):
        o = payments.create_order(self.store, "BASIC", "a@b.com", {})

        def _fail(**kw):
            raise RuntimeError("toss down")
        with self.assertRaises(payments.PaymentError):
            payments.approve_payment(self.store, "pk", o["orderId"], 9900, confirm_fn=_fail)
        self.assertEqual(self.store.get_order(o["orderId"])["status"], "FAILED")

    def test_approve_timeout_not_paid(self):
        o = payments.create_order(self.store, "BASIC", "a@b.com", {})

        def _timeout(**kw):
            raise TimeoutError("timed out")
        with self.assertRaises(payments.PaymentError):
            payments.approve_payment(self.store, "pk", o["orderId"], 9900, confirm_fn=_timeout)
        self.assertEqual(self.store.get_order(o["orderId"])["status"], "FAILED")

    def test_approve_wrong_toss_amount_not_paid(self):
        o = payments.create_order(self.store, "BASIC", "a@b.com", {})

        def _wrong(**kw):
            return {"orderId": kw["order_id"], "totalAmount": 1, "status": "DONE"}
        with self.assertRaises(payments.PaymentError):
            payments.approve_payment(self.store, "pk", o["orderId"], 9900, confirm_fn=_wrong)
        self.assertEqual(self.store.get_order(o["orderId"])["status"], "FAILED")

    def test_errors_do_not_leak_keys(self):
        try:
            payments.approve_payment(self.store, "pk", "ord_missing", 9900, confirm_fn=_toss_ok)
        except Exception as e:
            self.assertNotIn(_FERNET_KEY, str(e))
            self.assertNotIn("samplesecret", str(e))

    def test_toss_url_is_fixed_https(self):
        self.assertEqual(payments.TOSS_CONFIRM_URL, "https://api.tosspayments.com/v1/payments/confirm")

    def test_client_config_hides_secrets(self):
        cfg = payments.client_config()
        blob = str(cfg)
        self.assertNotIn("samplesecret", blob)      # secret key 미노출
        self.assertNotIn(_FERNET_KEY, blob)          # 암호화 키 미노출
        self.assertIn("clientKey", cfg)


class PaymentSqlSafetyTest(unittest.TestCase):
    def test_postgres_store_parameterized(self):
        with open(os.path.join(ROOT, "payments.py"), "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("%s", src)  # 파라미터 바인딩 사용
        # execute 호출에 f-string/%-포매팅으로 값 삽입하지 않음
        self.assertNotIn('cur.execute(f"', src)
        self.assertNotIn('cur.execute("SELECT * FROM orders WHERE order_id = \'" +', src)


class PaymentEndpointTest(unittest.TestCase):
    def setUp(self):
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        self.c = saju_bot.app.test_client()

    def tearDown(self):
        _clear_pay()

    def test_free_flow_no_db_calls(self):
        _clear_pay()
        self.c.get("/")
        self.c.post("/saju", json={"calendar": "solar", "birth_date": "1990-05-15",
                                   "birth_time": "08:30", "gender": "남"})
        self.assertEqual(saju_bot.ORDER_STORE.calls, 0)  # 무료 = DB 호출 0회

    def test_orders_disabled_returns_503(self):
        _clear_pay()
        r = self.c.post("/api/orders", json={"product_code": "BASIC", "email": "a@b.com"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(saju_bot.ORDER_STORE.calls, 0)

    def test_orders_enabled_creates_order(self):
        _enable_pay("test")
        r = self.c.post("/api/orders", json={
            "product_code": "EXPERT", "email": "a@b.com",
            "birth_date": "1990-05-15", "question": "x"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["amount"], 99000)
        self.assertTrue(body["orderId"].startswith("ord_"))

    def test_orders_forged_amount_ignored(self):
        _enable_pay("test")
        r = self.c.post("/api/orders", json={
            "product_code": "BASIC", "email": "a@b.com", "amount": 1})
        self.assertEqual(r.get_json()["amount"], 9900)

    def test_orders_bad_product_400(self):
        _enable_pay("test")
        r = self.c.post("/api/orders", json={"product_code": "FREE", "email": "a@b.com"})
        self.assertEqual(r.status_code, 400)

    def test_payment_success_mismatch_shows_fail(self):
        _enable_pay("test")
        o = payments.create_order(saju_bot.ORDER_STORE, "BASIC", "a@b.com", {})
        r = self.c.get("/payment/success?paymentKey=pk&orderId=%s&amount=1" % o["orderId"])
        self.assertEqual(r.status_code, 400)
        body = r.get_data(as_text=True)
        self.assertNotIn("samplesecret", body)

    def test_payment_fail_page_safe(self):
        r = self.c.get("/payment/fail?code=X&message=<script>")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertNotIn("<script>", body)  # 자동 이스케이프 / 미반영


class PaymentUITest(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_toss_sdk_only_when_enabled(self):
        html = self._read("templates/index.html")
        self.assertIn("{% if pay.enabled %}", html)
        self.assertIn("js.tosspayments.com/v2/standard", html)
        self.assertIn("window.PAY_CONFIG", html)

    def test_js_guards_payment_behind_config(self):
        js = self._read("static/app.js")
        self.assertIn("PAY_CONFIG", js)
        self.assertIn("ANONYMOUS", js)
        self.assertIn("/payment/success", js)
        self.assertIn("/payment/fail", js)
        self.assertIn("결제 기능 준비 중", js)   # 비활성 기본
        self.assertIn("테스트 결제하기", js)       # 활성 시에만

    def test_no_amount_deduction_claim(self):
        blob = self._read("static/app.js") + self._read("templates/payment_success.html")
        self.assertIn("실제 금액은 차감되지 않습니다", blob)
        self.assertNotIn("금액이 차감됩니다", blob)


class PaymentLaunchFixTest(unittest.TestCase):
    """결제창 호출 회귀: v2 Promise/동기 예외 모두 처리 + 안전한 콘솔 로깅.

    장애: /api/orders=200 이후 결제창 단계에서 예외가 catch 로 삼켜져(로그 없음)
    고객에게 '결제를 시작하지 못했습니다.'만 표시되고 콘솔에 단서가 남지 않았다.
    """

    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_v2_api_shape_not_mixed_with_v1(self):
        js = self._read("static/app.js")
        # v2 결제창형: TossPayments -> payment({customerKey}) -> requestPayment({method, amount:{...}})
        self.assertIn("TossPayments(window.PAY_CONFIG.clientKey)", js)
        self.assertIn("TossPayments.ANONYMOUS", js)
        self.assertIn("requestPayment(", js)
        self.assertIn('amount: { currency: "KRW"', js)  # v2 금액은 객체
        # v1 잔재가 섞이면 안 된다: 포지셔널 '카드', 숫자 amount, /v1/ SDK
        self.assertNotIn('requestPayment("카드"', js)
        self.assertNotIn("requestPayment('카드'", js)
        self.assertNotIn("js.tosspayments.com/v1", js)

    def test_request_payment_promise_and_sync_both_handled(self):
        js = self._read("static/app.js")
        # requestPayment 반환 Promise 처리(비동기 reject) + try/catch(동기 throw)
        self.assertIn('typeof req.then === "function"', js)
        self.assertIn("req.catch(onPayError)", js)
        self.assertIn("} catch (e) {", js)
        self.assertIn("onPayError(e)", js)

    def test_customer_email_passed_to_toss(self):
        js = self._read("static/app.js")
        self.assertIn("customerEmail: emailValue()", js)

    def test_catch_logs_code_and_message_only(self):
        js = self._read("static/app.js")
        self.assertIn("console.error", js)
        self.assertIn("code: err.code", js)
        self.assertIn("message: err.message", js)
        # 고객에게는 안전한 한국어 메시지만.
        self.assertIn("결제를 시작하지 못했습니다.", js)

    def test_console_calls_never_leak_secrets_or_pii(self):
        js = self._read("static/app.js")
        # 콘솔로 가는 모든 라인에 비밀값/이메일/주문자료가 섞이지 않아야 한다.
        forbidden = ["clientKey", "PAY_CONFIG", "emailValue(", "orderPayload(",
                     "o.email", "secret", "ORDER_ENCRYPTION"]
        for line in js.splitlines():
            if "console." in line:
                for bad in forbidden:
                    self.assertNotIn(bad, line,
                                     "console 라인에 민감정보 토큰 노출: %s" % line.strip())


class FreeInsightsTest(unittest.TestCase):
    """saju_insights: 순수 결정형 무료 요약 생성기."""

    _FEAR = ["죽음", "사망", "이혼", "파산", "질병", "공포", "위험합니다", "불행", "재앙"]

    def test_all_ten_day_masters_valid(self):
        gans = set()
        for d in range(1, 11):  # 10일 연속 -> 일간(日干) 10종 모두 등장
            saju = compute_saju("solar", "2000-01-%02d" % d, "08:30", "남")
            gans.add(saju["pillars"]["day"]["gan"])
            ins = saju_insights.build_free_result(saju, alias="바다", topics=["재물"])
            self.assertTrue(ins["persona_title"])
            self.assertEqual(len(ins["strengths"]), 3)
            self.assertEqual(len(ins["keywords"]), 3)
            for key in ("persona_sentence", "others_view", "relationship",
                        "caution", "suggestion", "element_note", "disclaimer"):
                self.assertTrue(ins[key], key)
            self.assertEqual(ins["reference_label"], "전통 명리학 관점의 자기이해 참고자료")
            self.assertEqual(ins["verification"], "NOT_VERIFIED")
        self.assertEqual(len(gans), 10)

    def test_deterministic_same_input_same_output(self):
        saju = compute_saju("solar", "1990-05-15", "08:30", "남")
        a = saju_insights.build_free_result(saju, alias="바다", topics=["재물", "직업", "연애"])
        b = saju_insights.build_free_result(saju, alias="바다", topics=["재물", "직업", "연애"])
        self.assertEqual(a, b)

    def test_topic_previews_max_two_each_two_lines(self):
        saju = compute_saju("solar", "1990-05-15", "08:30", "남")
        ins = saju_insights.build_free_result(saju, topics=["재물", "직업", "연애", "가족", "학업"])
        self.assertEqual(len(ins["topic_previews"]), 2)
        for tp in ins["topic_previews"]:
            self.assertEqual(len(tp["lines"]), 2)
        self.assertEqual(ins["paid_hint"],
                         "선택한 주제를 명식 근거와 현실적인 실행 제안까지 연결해 자세히 살펴봅니다.")

    def test_topic_alias_affection_maps_to_romance(self):
        saju = compute_saju("solar", "1990-05-15", "08:30", "남")
        ins = saju_insights.build_free_result(saju, topics=["애정"])
        self.assertEqual(ins["topic_previews"][0]["topic"], "연애")

    def test_unknown_topic_ignored(self):
        saju = compute_saju("solar", "1990-05-15", "08:30", "남")
        ins = saju_insights.build_free_result(saju, topics=["주식대박", "로또"])
        self.assertEqual(ins["topic_previews"], [])

    def test_time_missing_note_and_six_elements(self):
        saju = compute_saju("solar", "1990-05-15", None, "남")
        self.assertIsNone(saju["pillars"]["time"])
        ins = saju_insights.build_free_result(saju, topics=[])
        self.assertIn("시주", " ".join(ins["notes"]))
        self.assertEqual(sum(ins["elements"].values()), 6)  # 3주 x (간+지)

    def test_with_time_eight_elements(self):
        saju = compute_saju("solar", "1990-05-15", "08:30", "남")
        ins = saju_insights.build_free_result(saju, topics=[])
        self.assertEqual(sum(ins["elements"].values()), 8)  # 4주 x (간+지)

    def test_no_fear_language_across_all_topics(self):
        for d in range(1, 11):
            saju = compute_saju("solar", "2000-01-%02d" % d, "08:30", "남")
            ins = saju_insights.build_free_result(
                saju, topics=["재물", "직업", "연애", "가족", "학업", "건강", "올해의 흐름"])
            blob = repr(ins)
            for w in self._FEAR:
                self.assertNotIn(w, blob, "fear word leaked: %s" % w)

    def test_disclaimer_text(self):
        ins = saju_insights.build_free_result(compute_saju("solar", "1990-05-15", "08:30", "남"))
        self.assertIn("자기이해 참고자료", ins["disclaimer"])
        self.assertIn("의료·법률·재정", ins["disclaimer"])

    def test_alias_control_chars_stripped(self):
        ins = saju_insights.build_free_result(
            compute_saju("solar", "1990-05-15", "08:30", "남"), alias="바\x00다\x07")
        self.assertEqual(ins["alias"], "바다")

    def test_dominant_element_note(self):
        ins = saju_insights.build_free_result(compute_saju("solar", "1990-05-15", "08:30", "남"))
        # 오행 요약 문장에 한자 표기와 '기운'이 포함된다.
        self.assertIn("기운", ins["element_note"])

    def test_customer_wording_softened_but_status_kept(self):
        ins = saju_insights.build_free_result(compute_saju("solar", "1990-05-15", "08:30", "남"))
        joined = " ".join(ins["notes"])
        # 고객 화면 문구는 개발자식 표현(NOT_VERIFIED)을 노출하지 않는다.
        self.assertIn("출생시간과 해석 방식에 따라 일부 결과가 달라질 수 있는 참고자료입니다.", joined)
        self.assertNotIn("NOT_VERIFIED", joined)
        # 단, API 내부 정확성 상태 값은 유지한다.
        self.assertEqual(ins["verification"], "NOT_VERIFIED")


class FreeResultEndpointTest(unittest.TestCase):
    def setUp(self):
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        self.c = saju_bot.app.test_client()

    def _ok_payload(self, **over):
        p = {"calendar": "solar", "birth_date": "1990-05-15", "birth_time": "08:30",
             "gender": "남", "alias": "바다", "topics": ["재물", "직업"]}
        p.update(over)
        return p

    def test_free_insights_ok(self):
        r = self.c.post("/free-insights", json=self._ok_payload())
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("saju", body)
        self.assertIn("insight", body)
        self.assertEqual(len(body["insight"]["topic_previews"]), 2)

    def test_free_insights_no_db_calls(self):
        self.c.post("/free-insights", json=self._ok_payload())
        self.assertEqual(saju_bot.ORDER_STORE.calls, 0)  # 무료 = DB 호출 0회

    def test_free_insights_bad_input_400(self):
        r = self.c.post("/free-insights", json={"calendar": "solar", "birth_date": "bad", "gender": "남"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["status"], "error")

    def test_free_insights_insight_excludes_pii(self):
        r = self.c.post("/free-insights", json=self._ok_payload(email="user@example.com"))
        import json as _json
        insight_blob = _json.dumps(r.get_json()["insight"], ensure_ascii=False)
        self.assertNotIn("user@example.com", insight_blob)
        self.assertNotIn("1990-05-15", insight_blob)
        self.assertNotIn("남", insight_blob)

    def test_free_insights_time_missing_ok(self):
        r = self.c.post("/free-insights", json=self._ok_payload(birth_time=None))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.get_json()["saju"]["pillars"]["time"])


class FreeResultUITest(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_step6_container_present(self):
        self.assertIn('id="free-result"', self._read("templates/index.html"))

    def test_js_calls_free_insights(self):
        js = self._read("static/app.js")
        self.assertIn('fetchJSON("/free-insights"', js)
        self.assertIn("renderFreeResult", js)

    def test_js_result_sections_in_order_korean_first(self):
        js = self._read("static/app.js")
        for t in ["사주 한 장 요약", "타고난 강점", "사람들이 느끼는 모습", "관계 성향",
                  "조심하면 좋은 점", "오늘부터 적용할 제안", "관심 주제 미리보기", "오행 분포"]:
            self.assertIn(t, js)
        # 한국어 성향이 한자 명식보다 먼저 렌더된다.
        self.assertLess(js.index('"fr-persona"'), js.index("renderMyeongsikInto(ms"))

    def test_xss_alias_rendered_as_textcontent(self):
        js = self._read("static/app.js")
        self.assertIn("function el(", js)
        self.assertIn('el("h3", "fr-title", aliasName', js)  # 제목은 textContent 로 생성
        self.assertIn('box.innerHTML = ""', js)              # 초기화 후 el()/textContent 삽입
        self.assertNotIn("innerHTML = aliasName", js)

    def test_disclaimer_rendered(self):
        self.assertIn("ins.disclaimer", self._read("static/app.js"))

    def test_no_dev_json_shown(self):
        js = self._read("static/app.js")
        for bad in ["JSON.stringify(res", "customer_data", "/report-data"]:
            self.assertNotIn(bad, js)


class ProductDisplayTest(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_seven_products_retained(self):
        js = self._read("static/app.js")
        for pid in ["free", "basic_9900", "deep_39000", "expert_99000",
                    "life_290000", "relation_590000", "vip_990000"]:
            self.assertIn(pid, js)

    def test_prices_unchanged(self):
        js = self._read("static/app.js")
        for price in ["0원", "9,900원", "39,000원", "99,000원", "290,000원", "590,000원", "990,000원"]:
            self.assertIn(price, js)

    def test_representative_three_and_more_toggle(self):
        js = self._read("static/app.js")
        self.assertIn("REPRESENTATIVE_PRODUCTS", js)
        self.assertIn("프리미엄 보고서 더 보기", js)
        self.assertIn("product-extra", js)
        for pid in ["free", "basic_9900", "expert_99000"]:  # 대표 3개
            self.assertIn(pid, js)

    def test_toss_flow_unchanged(self):
        # 결제 테스트 흐름/토스 코드는 변경하지 않는다(회귀 가드).
        js = self._read("static/app.js")
        self.assertIn("TossPayments.ANONYMOUS", js)
        self.assertIn("customerEmail: emailValue()", js)
        self.assertIn("결제 기능 준비 중", js)


class ShareCardPrivacyTest(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_share_buttons_present(self):
        js = self._read("static/app.js")
        for t in ["결과 이미지 저장", "친구에게 공유", "링크 복사"]:
            self.assertIn(t, js)

    def test_share_uses_canvas_no_cdn(self):
        js = self._read("static/app.js")
        self.assertIn('createElement("canvas")', js)
        html = self._read("templates/index.html")
        self.assertNotIn("cdn", html.lower())  # 외부 CDN 추가 금지
        # 토스 SDK(결제 활성 시)만 외부 스크립트. 그 외 외부 script src 없음.
        self.assertEqual(html.count("<script src="), 2)  # app.js + 토스(조건부)

    def test_share_card_excludes_personal_info(self):
        js = self._read("static/app.js")
        body = js[js.index("function drawShareCard("):js.index("function shareStatusEl(")]
        for bad in ["alias", "email", "birth_date", "birth_time", "birth_place",
                    "gender", "val(", "emailValue", "pillars", "orderId", "lunar", "solar"]:
            self.assertNotIn(bad, body)
        for good in ["나의 사주 키워드", "persona_title", "keywords", "SERVICE_NAME",
                     "homepageUrl()", "당신의 사주 키워드는?"]:
            self.assertIn(good, body)

    def test_share_url_is_public_origin_only(self):
        js = self._read("static/app.js")
        self.assertIn("window.location.origin", js)
        for bad in ["?alias=", "?birth", "?email=", "?order", "?name="]:
            self.assertNotIn(bad, js)

    def test_no_client_storage_of_free_result(self):
        js = self._read("static/app.js")
        self.assertNotIn("localStorage", js)
        self.assertNotIn("sessionStorage", js)


class PaymentSuccessOrderNumberTest(unittest.TestCase):
    """결제 성공 화면에 문의용 주문번호 표시(paymentKey/이메일/내부값 비노출)."""

    def setUp(self):
        saju_bot.ORDER_STORE = payments.InMemoryOrderStore()
        self.c = saju_bot.app.test_client()

    def test_success_shows_order_number_not_paymentkey(self):
        saved = payments.approve_payment
        payments.approve_payment = lambda store, pk, oid, amt: {"status": "PAID", "orderId": oid, "idempotent": False}
        try:
            r = self.c.get("/payment/success?orderId=ord_testABC123&paymentKey=pk_secret_xyz&amount=9900")
        finally:
            payments.approve_payment = saved
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("ord_testABC123", body)   # 문의용 주문번호
        self.assertIn("주문번호 복사", body)        # 복사 버튼
        self.assertNotIn("pk_secret_xyz", body)  # paymentKey 미표시

    def test_success_order_number_xss_escaped(self):
        saved = payments.approve_payment
        payments.approve_payment = lambda *a, **k: {"status": "PAID", "orderId": "<script>alert(1)</script>"}
        try:
            r = self.c.get("/payment/success?orderId=x&paymentKey=p&amount=1")
        finally:
            payments.approve_payment = saved
        body = r.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_success_mismatch_still_fails(self):
        # 기존 승인 로직 회귀: 금액 불일치는 여전히 결제 실패 화면.
        _enable_pay("test")
        try:
            o = payments.create_order(saju_bot.ORDER_STORE, "BASIC", "a@b.com", {})
            r = self.c.get("/payment/success?paymentKey=pk&orderId=%s&amount=1" % o["orderId"])
        finally:
            _clear_pay()
        self.assertEqual(r.status_code, 400)


class MobileUXTest(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            return f.read()

    def test_viewport_meta(self):
        self.assertIn("width=device-width", self._read("templates/index.html"))

    def test_button_min_height_48(self):
        css = self._read("static/app.css")
        self.assertIn("min-height: 48px", css)

    def test_mobile_breakpoint_present(self):
        self.assertIn("max-width: 360px", self._read("static/app.css"))


class JieqiBoundaryTzTest(unittest.TestCase):
    """절기 경계 판정 회귀: 연·월주는 출생 순간을 절기 기준 시간대(UTC+8)로 변환해 판정하고,
    일·시주는 진태양시·sect 를 유지한다. (기준값은 작업 지시에서 제공된 회귀 앵커)"""

    def _p(self, t):
        r = compute_saju("solar", "2012-02-04", t, "남", longitude=126.978)
        p = r["pillars"]
        return r, (p["year"]["ganzhi"], p["month"]["ganzhi"],
                   p["day"]["ganzhi"], p["time"]["ganzhi"])

    def test_before_ipchun_19_10(self):
        r, gz = self._p("19:10")
        self.assertEqual(gz, ("辛卯", "辛丑", "乙未", "乙酉"))   # 입춘 전: 辛卯/辛丑
        self.assertIsNone(r["jieqi_boundary"])                  # 경계에서 12분↑ → 경고 없음

    def test_after_ipchun_19_23(self):
        r, gz = self._p("19:23")
        self.assertEqual(gz, ("壬辰", "壬寅", "乙未", "乙酉"))   # 입춘 후: 壬辰/壬寅
        self.assertIsNotNone(r["jieqi_boundary"])               # 경계 근접(약 36초) → 경고
        self.assertEqual(r["jieqi_boundary"]["type"], "jieqi_minute_boundary")

    def test_day_hour_unchanged_across_boundary(self):
        # 분 차이로 연·월주만 바뀌고 일·시주는 동일(진태양시·sect 유지).
        _, a = self._p("19:10")
        _, b = self._p("19:23")
        self.assertEqual((a[2], a[3]), (b[2], b[3]))            # 일·시주 동일

    def test_year_month_uses_historical_tz_not_wallclock(self):
        # 벽시계(KST)를 그대로 넣었다면 19:10 도 입춘 후(壬寅)로 잘못 판정됐을 것.
        # 절기 기준 변환으로 19:10 은 입춘 전(辛丑)이어야 한다.
        _, gz = self._p("19:10")
        self.assertEqual(gz[1], "辛丑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
