"""사주(四柱, Four Pillars) 계산 엔진.

lunar_python (MIT, (c) 2020 6tail) 기반 순수 계산 모듈.
- 네트워크 호출 / 파일 쓰기 / 환경변수 접근 / subprocess / eval·exec 없음.
- 입력(생년월일·시간·성별)을 저장하거나 로그에 남기지 않는다.
- 대한민국 출생자의 입력 현지시각(KST 벽시계)을 그대로 사용한다.
  진태양시·출생지 경도 보정은 이번 단계에서 하지 않는다.

주의: 라이브러리가 결과를 반환한다는 것과 한국 만세력 정확성이 검증됐다는 것은
다르다. 신뢰 기준값 교차검증 전까지 "정확한 한국 만세력"으로 표시하지 않는다.
"""

import datetime

from lunar_python import Solar, Lunar

SUPPORTED_YEAR_MIN = 1900
SUPPORTED_YEAR_MAX = 2100

# 자시(子時) 규칙: sect=2 는 자정(00:00) 기준 일자 전환(기본값), sect=1 은 23:00 전환.
DEFAULT_SECT = 2

GAN_KO = {
    "甲": "갑", "乙": "을", "丙": "병", "丁": "정", "戊": "무",
    "己": "기", "庚": "경", "辛": "신", "壬": "임", "癸": "계",
}
ZHI_KO = {
    "子": "자", "丑": "축", "寅": "인", "卯": "묘", "辰": "진", "巳": "사",
    "午": "오", "未": "미", "申": "신", "酉": "유", "戌": "술", "亥": "해",
}


class SajuInputError(ValueError):
    """사용자 입력 오류. 라우트 계층에서 HTTP 400으로 변환한다."""


def _pillar(gan, zhi):
    return {
        "ganzhi": "%s%s" % (gan, zhi),
        "gan": gan,
        "zhi": zhi,
        "gan_ko": GAN_KO.get(gan, ""),
        "zhi_ko": ZHI_KO.get(zhi, ""),
    }


def _day_time(ec):
    return {
        "day": _pillar(ec.getDayGan(), ec.getDayZhi()),
        "time": _pillar(ec.getTimeGan(), ec.getTimeZhi()),
    }


def _parse_date(birth_date):
    if not isinstance(birth_date, str) or len(birth_date) != 10 or birth_date[4] != "-" or birth_date[7] != "-":
        raise SajuInputError("birth_date는 'YYYY-MM-DD' 형식이어야 합니다.")
    try:
        y = int(birth_date[0:4])
        m = int(birth_date[5:7])
        d = int(birth_date[8:10])
    except ValueError:
        raise SajuInputError("birth_date는 'YYYY-MM-DD' 형식이어야 합니다.")
    if not (SUPPORTED_YEAR_MIN <= y <= SUPPORTED_YEAR_MAX):
        raise SajuInputError("지원 연도 범위(%d-%d)를 벗어났습니다." % (SUPPORTED_YEAR_MIN, SUPPORTED_YEAR_MAX))
    if not (1 <= m <= 12):
        raise SajuInputError("월은 1-12 사이여야 합니다.")
    if not (1 <= d <= 31):
        raise SajuInputError("일은 1-31 사이여야 합니다.")
    return y, m, d


def _parse_time(birth_time):
    """반환: (has_time, hour, minute)."""
    if birth_time is None or birth_time == "":
        return False, 0, 0
    if not isinstance(birth_time, str) or len(birth_time) != 5 or birth_time[2] != ":":
        raise SajuInputError("birth_time은 'HH:MM' 형식이어야 합니다.")
    try:
        hh = int(birth_time[0:2])
        mm = int(birth_time[3:5])
    except ValueError:
        raise SajuInputError("birth_time은 'HH:MM' 형식이어야 합니다.")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise SajuInputError("birth_time은 00:00-23:59 범위여야 합니다.")
    return True, hh, mm


def _normalize_gender(gender):
    mapping = {"남": "남", "여": "여", "male": "남", "female": "여", "m": "남", "f": "여"}
    if not isinstance(gender, str):
        raise SajuInputError("gender는 '남' 또는 '여'여야 합니다.")
    key = gender.strip().lower() if gender.strip().lower() in ("male", "female", "m", "f") else gender.strip()
    if key not in mapping:
        raise SajuInputError("gender는 '남' 또는 '여'여야 합니다.")
    return mapping[key]


def compute_saju(calendar="solar", birth_date=None, birth_time=None, gender=None, is_leap_month=False):
    """사주 4주를 계산해 dict로 반환한다. 입력 오류는 SajuInputError를 던진다."""
    if calendar not in ("solar", "lunar"):
        raise SajuInputError("calendar는 'solar' 또는 'lunar'여야 합니다.")

    if is_leap_month is None:
        is_leap_month = False
    if not isinstance(is_leap_month, bool):
        raise SajuInputError("is_leap_month는 boolean이어야 합니다.")
    if calendar == "solar" and is_leap_month:
        raise SajuInputError("is_leap_month는 음력(lunar)에서만 사용할 수 있습니다.")

    y, m, d = _parse_date(birth_date)
    has_time, hour, minute = _parse_time(birth_time)
    norm_gender = _normalize_gender(gender)

    if calendar == "solar":
        try:
            datetime.date(y, m, d)
        except ValueError:
            raise SajuInputError("존재하지 않는 양력 날짜입니다.")
        solar = Solar.fromYmdHms(y, m, d, hour, minute, 0)
        lunar = solar.getLunar()
    else:
        month_arg = -m if is_leap_month else m
        try:
            lunar = Lunar.fromYmdHms(y, month_arg, d, hour, minute, 0)
            solar = lunar.getSolar()
            back = solar.getLunar()
        except Exception:
            # 라이브러리가 존재하지 않는 음력/윤달을 거부하면 입력 오류로 간주한다.
            raise SajuInputError("존재하지 않는 음력 날짜 또는 윤달입니다.")
        # 역변환 일치 검증(연·월·일·윤달 여부).
        if (back.getYear(), back.getMonth(), back.getDay()) != (y, month_arg, d):
            raise SajuInputError("존재하지 않는 음력 날짜 또는 윤달입니다.")

    ec = lunar.getEightChar()
    ec.setSect(DEFAULT_SECT)

    pillars = {
        "year": _pillar(ec.getYearGan(), ec.getYearZhi()),
        "month": _pillar(ec.getMonthGan(), ec.getMonthZhi()),
        "day": _pillar(ec.getDayGan(), ec.getDayZhi()),
        "time": _pillar(ec.getTimeGan(), ec.getTimeZhi()) if has_time else None,
    }

    boundary_warning = None
    if has_time and hour == 23:
        ec.setSect(1)
        sect1 = _day_time(ec)
        ec.setSect(2)
        sect2 = _day_time(ec)
        boundary_warning = {
            "type": "late_zi_23h",
            "message": (
                "23시대(23:00-23:59) 출생은 자시(子時) 규칙에 따라 일주·시주가 달라질 수 있습니다. "
                "기본값은 sect=2(자정 00:00 전환)입니다."
            ),
            "sect1": sect1,
            "sect2": sect2,
        }
        # pillars 는 기본 sect=2 결과를 유지한다.
        pillars["day"] = sect2["day"]
        pillars["time"] = sect2["time"]

    lunar_month = lunar.getMonth()
    lunar_text = "음력 %d년 %d월%s %d일" % (
        lunar.getYear(),
        abs(lunar_month),
        "(윤달)" if lunar_month < 0 else "",
        lunar.getDay(),
    )

    result = {
        "status": "ok",
        "input": {
            "calendar": calendar,
            "birth_date": birth_date,
            "birth_time": birth_time if has_time else None,
            "gender": norm_gender,
            "is_leap_month": is_leap_month,
        },
        "solar": {
            "year": solar.getYear(),
            "month": solar.getMonth(),
            "day": solar.getDay(),
            "time": birth_time if has_time else None,
        },
        "lunar": {
            "year": lunar.getYear(),
            "month": abs(lunar_month),
            "is_leap_month": lunar_month < 0,
            "day": lunar.getDay(),
            "text": lunar_text,
        },
        "sect": DEFAULT_SECT,
        "convention": {
            "timezone": "KST-wallclock",
            "longitude_correction": False,
            "zi_rule": "sect=2(00:00)",
        },
        "pillars": pillars,
        "boundary_warning": boundary_warning,
        "accuracy_note": "초기 버전: 한국 만세력 기준 정확성은 아직 교차검증되지 않았습니다.",
    }

    tp = pillars["time"]["ganzhi"] if pillars["time"] else "미상"
    result["message"] = "사주 산출 완료: 연 %s 월 %s 일 %s 시 %s" % (
        pillars["year"]["ganzhi"],
        pillars["month"]["ganzhi"],
        pillars["day"]["ganzhi"],
        tp,
    )
    return result
