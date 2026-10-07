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

    def test_expert_badge_only_marked_products(self):
        js = self._read("static/app.js")
        self.assertIn("전문가 검토", js)
        self.assertIn("AI 기반 자동 해석", js)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
