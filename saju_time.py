"""진태양시(True Solar Time) 보정 계층 — 대한민국 전용(초기 버전).

법정 출생시각(벽시계)을 사주 4주 계산에 쓰이는 진태양시로 변환한다.
보정 = 법정시각 − 서머타임(DST) + 경도 보정 + 균시차(Equation of Time).

구성 요소:
- 역사적 UTC offset / DST: IANA tz 데이터(Asia/Seoul)를 zoneinfo로 조회.
  · 표준시 offset = utcoffset − dst
  · 기준 자오선 = (표준시 offset 시간) × 15°
  · 한국은 1954-1961 UTC+8:30(127.5°E), 그 외 UTC+9(135°E).
    1948-51·1955-60·1987-88 등 DST 구간은 tz 데이터가 반영.
- 경도 보정: 4 × (출생지 경도 − 기준 자오선) 분.
- 균시차: NOAA 계열 결정론적 근사식(분).

한계:
- 대한민국(Asia/Seoul)만 지원. 허용 경도 124.0~132.0.
- 균시차는 근사식(정밀도 ~수십 초). 절기/자시 경계에 분 단위로 근접한
  출생은 결과가 민감할 수 있다.
- 외부 네트워크/지도 API/환경변수/파일쓰기 없음. 순수 계산.
"""

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
LONGITUDE_MIN = 124.0
LONGITUDE_MAX = 132.0


class TimeCorrectionError(ValueError):
    """진태양시 보정 입력 오류(경도 범위 등). 라우트에서 HTTP 400."""


def equation_of_time_minutes(year_day, frac_hour):
    """NOAA 균시차 근사(분). year_day=연중일(1~366), frac_hour=시+분/60."""
    gamma = 2.0 * math.pi / 365.0 * (year_day - 1 + (frac_hour - 12.0) / 24.0)
    eot = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    return eot


def compute_true_solar(local_date, hour, minute, longitude):
    """법정시각 -> 진태양시 보정 결과 dict.

    local_date: datetime.date (법정 civil 날짜)
    hour, minute: 법정 벽시계 시/분
    longitude: 출생지 경도(동경, 도)
    """
    if not (LONGITUDE_MIN <= longitude <= LONGITUDE_MAX):
        raise TimeCorrectionError(
            "경도는 %.1f~%.1f 범위여야 합니다(대한민국)." % (LONGITUDE_MIN, LONGITUDE_MAX)
        )

    naive = datetime(local_date.year, local_date.month, local_date.day, hour, minute)
    aware = naive.replace(tzinfo=KST)
    utc_off_min = aware.utcoffset().total_seconds() / 60.0
    dst_min = aware.dst().total_seconds() / 60.0
    std_off_min = utc_off_min - dst_min
    std_meridian = std_off_min / 60.0 * 15.0

    longitude_correction = 4.0 * (longitude - std_meridian)
    year_day = local_date.timetuple().tm_yday
    eot = equation_of_time_minutes(year_day, hour + minute / 60.0)

    standard_local = naive - timedelta(minutes=dst_min)
    total_shift = -dst_min + longitude_correction + eot
    true_solar = naive + timedelta(minutes=total_shift)

    return {
        "applied": True,
        "timezone": "Asia/Seoul",
        "original_local": naive.strftime("%Y-%m-%d %H:%M"),
        "standard_local": standard_local.strftime("%Y-%m-%d %H:%M"),
        "true_solar_local": true_solar.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_offset_minutes": round(utc_off_min, 3),
        "dst_minutes": round(dst_min, 3),
        "standard_meridian": round(std_meridian, 3),
        "longitude": longitude,
        "longitude_correction_minutes": round(longitude_correction, 3),
        "equation_of_time_minutes": round(eot, 3),
        "method": "IANA Asia/Seoul + longitude + equation-of-time",
        # 엔진이 4주 계산에 사용할 보정된 날짜/시각(초 포함)
        "_true_solar_dt": true_solar,
    }
