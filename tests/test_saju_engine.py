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
        for token in ["생년월일", "태어난 시간", "성별", "양력", "음력", "윤달", "/saju"]:
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

    FILES = ["saju_engine.py", "saju_bot.py"]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
