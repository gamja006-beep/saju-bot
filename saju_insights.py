"""무료 사주 요약용 해석 생성기 (순수 결정형, 외부 의존 없음).

원칙:
- 네트워크/파일/환경변수/AI/난수/시계 사용 금지. 같은 입력 -> 항상 같은 결과.
- 일간(日干) 10종과 오행(五行) 분포 기반의 짧은 템플릿만 사용한다.
- 긍정 70% / 주의 30% 균형. 공포·사고·질병·죽음·이혼·파산 등 불안 조장 표현 금지.
- 미래를 확정적으로 예언하지 않는다.
- "전통 명리학 관점의 자기이해 참고자료"이며 전체 정확성은 NOT_VERIFIED.

이 모듈은 compute_saju() 결과(dict)를 입력으로 받아 화면 표시용 구조만 반환한다.
개인정보(생년월일·시간·지역·성별·이메일)는 요약에 담지 않으며, 별칭도 화면 제목에만 쓰고
공유 카드/URL에는 쓰지 않는다(프런트 책임). 저장·로그·외부 호출 없음.
"""

REFERENCE_LABEL = "전통 명리학 관점의 자기이해 참고자료"
VERIFICATION = "NOT_VERIFIED"
DISCLAIMER = (
    "이 결과는 전통 명리학 관점의 자기이해 참고자료이며, "
    "중요한 의료·법률·재정적 결정을 대신하지 않습니다."
)
PAID_HINT = "선택한 주제를 명식 근거와 현실적인 실행 제안까지 연결해 자세히 살펴봅니다."

# ---- 오행 매핑 ----
LABEL_HANJA = {"목": "木", "화": "火", "토": "土", "금": "金", "수": "水"}
ELEMENT_ORDER = ["목", "화", "토", "금", "수"]

GAN_ELEMENT = {
    "甲": "목", "乙": "목", "丙": "화", "丁": "화", "戊": "토",
    "己": "토", "庚": "금", "辛": "금", "壬": "수", "癸": "수",
}
ZHI_ELEMENT = {
    "子": "수", "丑": "토", "寅": "목", "卯": "목", "辰": "토", "巳": "화",
    "午": "화", "未": "토", "申": "금", "酉": "금", "戌": "토", "亥": "수",
}
GAN_POLARITY = {
    "甲": "양", "丙": "양", "戊": "양", "庚": "양", "壬": "양",
    "乙": "음", "丁": "음", "己": "음", "辛": "음", "癸": "음",
}
GAN_KO = {
    "甲": "갑", "乙": "을", "丙": "병", "丁": "정", "戊": "무",
    "己": "기", "庚": "경", "辛": "신", "壬": "임", "癸": "계",
}

ELEMENT_PHRASE = {
    "목": "성장과 추진", "화": "표현과 열정", "토": "안정과 신뢰",
    "금": "결단과 정리", "수": "지혜와 유연함",
}

# ---- 일간(日干) 10종 성향 템플릿 ----
# caution/suggestion 은 constructively(비공포) 작성. strengths 3개, keywords 3개(공유 카드용).
GAN_PROFILE = {
    "甲": {
        "title": "곧게 자라는 큰 나무",
        "persona": "전통 명리학에서 갑목(甲木)은 곧게 뻗는 큰 나무에 비유되며, 중심을 세우고 앞장서 나아가는 성향으로 봅니다.",
        "strengths": ["방향을 정하면 밀고 나가는 추진력", "원칙과 중심이 뚜렷함", "주변을 이끄는 든든한 책임감"],
        "keywords": ["추진력", "리더십", "곧은 심지"],
        "others": "사람들은 믿음직하고 든든한 사람으로 느끼는 경우가 많습니다.",
        "relationship": "한번 맺은 관계를 오래 지키려 하고, 먼저 방향을 제시하는 편입니다.",
        "caution": "혼자 다 짊어지기보다 주변에 역할을 나눠 맡기면 한결 가벼워집니다.",
        "suggestion": "오늘 할 일 중 하나는 다른 사람에게 맡겨 보세요.",
    },
    "乙": {
        "title": "유연하게 뻗는 풀과 넝쿨",
        "persona": "을목(乙木)은 부드럽게 휘면서도 끝내 자라나는 풀에 비유되며, 상황에 맞춰 유연하게 적응하는 성향으로 봅니다.",
        "strengths": ["환경에 맞추는 뛰어난 적응력", "곁을 살피는 섬세한 배려", "끈기 있게 이어가는 힘"],
        "keywords": ["유연함", "섬세함", "끈기"],
        "others": "주변과 잘 어울리고 분위기를 편안하게 만드는 사람으로 보입니다.",
        "relationship": "상대의 결에 맞춰 주며 관계를 부드럽게 유지합니다.",
        "caution": "남에게 맞추다 내 바람을 미루기 쉬우니, 원하는 것을 한 번씩 말로 표현해 보면 좋습니다.",
        "suggestion": "오늘 작은 것 하나는 내 취향대로 골라 보세요.",
    },
    "丙": {
        "title": "환하게 비추는 태양",
        "persona": "병화(丙火)는 온 세상을 밝히는 태양에 비유되며, 밝고 활기차게 주변을 환하게 만드는 성향으로 봅니다.",
        "strengths": ["밝은 에너지와 표현력", "사람을 끌어모으는 활기", "솔직하고 분명한 태도"],
        "keywords": ["밝음", "활기", "표현력"],
        "others": "함께 있으면 기운이 난다는 말을 자주 듣는 편입니다.",
        "relationship": "애정을 숨기지 않고 따뜻하게 표현합니다.",
        "caution": "감정이 빠르게 올라오는 편이라, 한 박자 쉬고 말하면 전달이 더 잘 됩니다.",
        "suggestion": "오늘 중요한 말은 숨을 한 번 고른 뒤 꺼내 보세요.",
    },
    "丁": {
        "title": "따뜻하게 밝히는 등불",
        "persona": "정화(丁火)는 어둠 속을 은은히 밝히는 등불에 비유되며, 가까운 곳을 깊고 따뜻하게 살피는 성향으로 봅니다.",
        "strengths": ["깊이 있는 집중력", "결을 읽는 세심한 관찰력", "진심 어린 온기"],
        "keywords": ["집중", "온기", "세심함"],
        "others": "조용하지만 속이 깊은 사람으로 느껴집니다.",
        "relationship": "소수의 사람과 깊고 오래가는 관계를 선호합니다.",
        "caution": "생각이 안으로 깊어질 때가 있으니, 마음을 가끔 밖으로 꺼내 나누면 가벼워집니다.",
        "suggestion": "오늘 떠오른 생각 하나를 가까운 사람에게 말해 보세요.",
    },
    "戊": {
        "title": "넉넉한 큰 산과 대지",
        "persona": "무토(戊土)는 흔들림 없는 큰 산에 비유되며, 묵직하게 중심을 잡고 주변을 품는 성향으로 봅니다.",
        "strengths": ["한결같은 안정감", "믿고 기댈 수 있는 신뢰", "넓게 품는 포용력"],
        "keywords": ["안정감", "신뢰", "포용"],
        "others": "어떤 상황에도 든든하게 버텨 주는 사람으로 보입니다.",
        "relationship": "서두르지 않고 천천히, 오래 가는 관계를 만듭니다.",
        "caution": "변화 앞에서 천천히 움직이는 편이니, 작은 시도를 먼저 해 보면 수월합니다.",
        "suggestion": "오늘 미뤄 둔 작은 변화 하나를 가볍게 시작해 보세요.",
    },
    "己": {
        "title": "곡식을 길러내는 기름진 흙",
        "persona": "기토(己土)는 씨앗을 길러내는 기름진 밭에 비유되며, 꼼꼼히 돌보고 실속 있게 가꾸는 성향으로 봅니다.",
        "strengths": ["차분한 현실 감각", "꾸준히 돌보는 성실함", "실용적인 문제 해결력"],
        "keywords": ["성실", "실용", "돌봄"],
        "others": "맡은 일을 야무지게 해내는 사람으로 보입니다.",
        "relationship": "조용히 챙겨 주며 신뢰를 쌓아 갑니다.",
        "caution": "혼자 다 챙기려 애쓰기보다, 도움을 청하는 연습을 하면 여유가 생깁니다.",
        "suggestion": "오늘 한 가지는 '도와 달라'고 먼저 말해 보세요.",
    },
    "庚": {
        "title": "단단하게 벼려진 쇠",
        "persona": "경금(庚金)은 아직 다듬어지지 않은 단단한 쇠에 비유되며, 분명하게 끊고 맺으며 밀고 나가는 성향으로 봅니다.",
        "strengths": ["과감한 결단력", "한번 맺으면 지키는 의리", "어려움을 돌파하는 힘"],
        "keywords": ["결단력", "의리", "돌파력"],
        "others": "시원시원하고 믿을 만한 사람으로 느껴집니다.",
        "relationship": "한번 마음을 주면 끝까지 챙기는 의리파입니다.",
        "caution": "말이 곧게 나갈 때가 있으니, 한 마디 부드럽게 덧붙이면 관계가 매끄럽습니다.",
        "suggestion": "오늘 누군가에게 고마움을 한마디 표현해 보세요.",
    },
    "辛": {
        "title": "정교하게 빛나는 보석",
        "persona": "신금(辛金)은 섬세하게 세공된 보석에 비유되며, 또렷한 기준과 감각으로 완성도를 높이는 성향으로 봅니다.",
        "strengths": ["뛰어난 감각과 안목", "깔끔한 완성도", "또렷한 자기 기준"],
        "keywords": ["감각", "정교함", "안목"],
        "others": "세련되고 단정한 사람으로 보입니다.",
        "relationship": "가까운 사람에게 특히 세심하게 마음을 씁니다.",
        "caution": "스스로에게 기준이 높은 편이라, 잘한 점을 먼저 인정해 주면 마음이 편안해집니다.",
        "suggestion": "오늘 해낸 일 하나를 스스로 칭찬해 보세요.",
    },
    "壬": {
        "title": "넓게 흐르는 큰 물",
        "persona": "임수(壬水)는 쉼 없이 흐르는 큰 강물에 비유되며, 넓게 보고 유연하게 흐름을 타는 성향으로 봅니다.",
        "strengths": ["폭넓은 시야와 지혜", "변화에 능한 유연함", "샘솟는 아이디어"],
        "keywords": ["지혜", "유연함", "아이디어"],
        "others": "생각이 깊고 융통성 있는 사람으로 보입니다.",
        "relationship": "다양한 사람과 두루 잘 어울립니다.",
        "caution": "관심이 여러 갈래로 흐를 수 있으니, 하나를 끝맺는 즐거움도 챙겨 보세요.",
        "suggestion": "오늘 벌여 둔 일 중 하나를 매듭지어 보세요.",
    },
    "癸": {
        "title": "촉촉이 스며드는 맑은 물",
        "persona": "계수(癸水)는 조용히 대지를 적시는 이슬비에 비유되며, 섬세한 감수성과 직관으로 스며드는 성향으로 봅니다.",
        "strengths": ["풍부한 감수성", "앞을 헤아리는 직관", "차분한 집중"],
        "keywords": ["감수성", "직관", "차분함"],
        "others": "조용하지만 속 깊고 센스 있는 사람으로 보입니다.",
        "relationship": "상대의 기분을 잘 알아채고 맞춰 줍니다.",
        "caution": "마음에 담아 두기 쉬우니, 느낌을 가볍게 나누면 한결 산뜻해집니다.",
        "suggestion": "오늘 느낀 기분 하나를 짧게 적거나 말해 보세요.",
    },
}

# ---- 관심 주제 미리보기 (주제별 2문장, 비예언·비공포) ----
TOPIC_ALIAS = {"애정": "연애"}
TOPIC_PREVIEWS = {
    "재물": [
        "재물운은 '얼마를 버느냐'보다 '어떤 리듬으로 모으고 지키느냐'를 함께 봅니다.",
        "무리한 한 방보다, 꾸준한 저축·관리 습관이 당신에게 더 잘 맞는 방향입니다.",
    ],
    "직업": [
        "직업은 적성과 일하는 방식의 결이 맞을 때 더 오래 즐겁게 이어집니다.",
        "강점으로 가진 성향을 살릴 수 있는 역할에서 만족도가 높아지는 편입니다.",
    ],
    "연애": [
        "연애는 상대를 바꾸려 하기보다 서로의 속도를 맞출 때 편안해집니다.",
        "마음을 솔직하되 부드럽게 표현하는 연습이 관계를 단단하게 합니다.",
    ],
    "가족": [
        "가족 관계는 자주 표현하는 작은 관심에서 신뢰가 쌓입니다.",
        "서로의 역할을 조금씩 나누면 한 사람에게 부담이 몰리지 않습니다.",
    ],
    "학업": [
        "학업은 긴 시간보다 집중하는 리듬을 만드는 것이 중요합니다.",
        "자신에게 맞는 시간대와 환경을 찾으면 같은 노력으로 더 큰 효과를 냅니다.",
    ],
    "건강": [
        "건강은 특별한 관리보다 매일의 작은 리듬(수면·식사·움직임)에서 시작됩니다.",
        "무리하지 않는 선에서 규칙적인 생활 습관을 하나씩 더해 보세요.",
    ],
    "올해의 흐름": [
        "올해는 큰 결정을 서두르기보다, 방향을 점검하고 준비하기 좋은 시기로 봅니다.",
        "작게 시작한 변화가 한 해 동안 꾸준히 쌓이면 의미 있는 결과로 이어집니다.",
    ],
}

_MAX_TOPIC_PREVIEWS = 2


def _safe_alias(alias):
    """제어문자 제거 + 길이 제한(화면 제목 전용). 공유 카드/URL 에는 쓰지 않는다."""
    if not isinstance(alias, str):
        return ""
    out = []
    for ch in alias:
        o = ord(ch)
        if o < 32 or o == 127:  # 제어문자 제거
            continue
        out.append(ch)
    return "".join(out).strip()[:50]


def _element_distribution(pillars):
    """현재 존재하는 주(柱)의 천간+지지 오행 개수를 센다. time 이 없으면 제외한다."""
    counts = {k: 0 for k in ELEMENT_ORDER}
    for key in ("year", "month", "day", "time"):
        p = pillars.get(key)
        if not p:
            continue
        ge = GAN_ELEMENT.get(p.get("gan"))
        ze = ZHI_ELEMENT.get(p.get("zhi"))
        if ge:
            counts[ge] += 1
        if ze:
            counts[ze] += 1
    return counts


def _element_note(counts):
    present = [(counts[k], -ELEMENT_ORDER.index(k), k) for k in ELEMENT_ORDER]
    present.sort(reverse=True)
    dom = present[0][2]
    note = "오행 분포에서 %s(%s) 기운이 가장 도드라져, %s의 결이 강하게 나타납니다." % (
        dom, LABEL_HANJA[dom], ELEMENT_PHRASE[dom])
    zeros = [k for k in ELEMENT_ORDER if counts[k] == 0]
    if zeros:
        z = zeros[0]
        note += " 반면 %s(%s) 기운은 옅은 편이라, 그와 어울리는 작은 습관을 더하면 균형에 도움이 됩니다." % (
            z, LABEL_HANJA[z])
    return note


def _topic_previews(topics):
    if not isinstance(topics, (list, tuple)):
        return []
    out = []
    used = set()
    for t in topics:
        if not isinstance(t, str):
            continue
        canon = TOPIC_ALIAS.get(t.strip(), t.strip())
        if canon in TOPIC_PREVIEWS and canon not in used:
            used.add(canon)
            out.append({"topic": canon, "lines": list(TOPIC_PREVIEWS[canon])})
        if len(out) >= _MAX_TOPIC_PREVIEWS:
            break
    return out


def _time_notes(saju):
    notes = []
    pillars = saju.get("pillars", {})
    if not pillars.get("time"):
        notes.append("태어난 시간을 입력하지 않아 시주(時柱)는 계산하지 않았고, 시간과 관련된 해석은 참고에서 제외했습니다.")
    if saju.get("needs_confirmation"):
        notes.append("출생지(경도)를 입력하지 않아 진태양시 보정이 적용되지 않았습니다. 경계 시간대에서는 결과가 달라질 수 있습니다.")
    if saju.get("boundary_warning"):
        notes.append("밤 11시대(23:00~23:59) 출생은 자시(子時) 기준에 따라 일부 해석이 달라질 수 있습니다.")
    if saju.get("jieqi_boundary"):
        notes.append("절기(월·연이 바뀌는 절입) 경계에 가까운 출생이라 연주·월주가 달라질 수 있어 참고로만 봐 주세요.")
    notes.append("출생시간과 해석 방식에 따라 일부 결과가 달라질 수 있는 참고자료입니다.")
    return notes


def build_free_result(saju, alias=None, topics=None):
    """compute_saju 결과로 무료 요약 구조를 만든다. 순수 함수(같은 입력 -> 같은 출력)."""
    pillars = saju.get("pillars", {})
    day = pillars.get("day") or {}
    gan = day.get("gan")
    profile = GAN_PROFILE.get(gan)
    if profile is None:
        # 방어적 기본값(정상 입력에서는 도달하지 않음).
        profile = {
            "title": "균형을 찾아가는 사람",
            "persona": "전통 명리학 관점에서 성향을 요약하기 위한 기준 정보가 충분하지 않습니다.",
            "strengths": ["차분함", "균형 감각", "유연함"],
            "keywords": ["균형", "차분함", "유연함"],
            "others": "상황에 따라 다양한 모습을 보이는 사람으로 보입니다.",
            "relationship": "상대에 맞춰 관계를 조율하는 편입니다.",
            "caution": "자신의 기준을 한 번씩 점검하면 방향이 또렷해집니다.",
            "suggestion": "오늘 하나의 작은 선택을 스스로 정해 보세요.",
        }

    counts = _element_distribution(pillars)

    return {
        "reference_label": REFERENCE_LABEL,
        "verification": VERIFICATION,
        "alias": _safe_alias(alias),
        "day_master": {
            "gan": gan or "",
            "gan_ko": GAN_KO.get(gan, ""),
            "element": GAN_ELEMENT.get(gan, ""),
            "polarity": GAN_POLARITY.get(gan, ""),
        },
        "persona_title": profile["title"],
        "persona_sentence": profile["persona"],
        "strengths": list(profile["strengths"]),
        "keywords": list(profile["keywords"]),
        "others_view": profile["others"],
        "relationship": profile["relationship"],
        "caution": profile["caution"],
        "suggestion": profile["suggestion"],
        "elements": counts,
        "element_note": _element_note(counts),
        "topic_previews": _topic_previews(topics),
        "paid_hint": PAID_HINT,
        "notes": _time_notes(saju),
        "disclaimer": DISCLAIMER,
    }
