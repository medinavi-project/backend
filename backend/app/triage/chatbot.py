import os
import re
import json
from dotenv import load_dotenv
from openai import OpenAI

from app.triage.dept_mapping import departments_for_condition, find_condition
from app.hospital.dept_codes import STANDARD_DEPARTMENTS, normalize_department

load_dotenv()
API_KEY = os.getenv("CLOVASTUDIO_API_KEY")

client = OpenAI(
    api_key=API_KEY or "unset",  # MediNavi: import must not fail without a key
    base_url="https://clovastudio.stream.ntruss.com/v1/openai"
)

DEFAULT_REGION = "경기도 시흥시"
ALLOWED_FIELDS = (
    "main_symptom",
    "body_location",
    "onset",
    "severity",
    "additional_symptoms",
    "region",
)
CORE_SYMPTOM_FIELDS = (
    "main_symptom",
    "body_location",
    "onset",
    "severity",
    "additional_symptoms",
)
FIELD_LABELS = {
    "main_symptom": "주증상",
    "body_location": "부위",
    "onset": "발생 시점",
    "severity": "통증 강도",
    "additional_symptoms": "동반 증상",
}
FIELD_QUESTIONS = {
    "main_symptom": "가장 불편하신 주된 증상은 무엇인가요?",
    "body_location": "불편한 증상이 나타나는 구체적인 부위는 어디인가요?",
    "onset": "증상은 언제부터 시작되었나요?",
    "severity": "통증이나 불편감의 강도는 0에서 10 중 어느 정도인가요?",
    "additional_symptoms": "동반되는 다른 증상(발열, 구토, 설사 등)이 있나요?",
}

# ── 서버 내부 상태 키 ──
# current_state에 같이 저장하지만 응답(symptom_updates)이나 LLM 프롬프트의 증상 상태에는 넣지 않는다.
# 호출 측이 state를 다시 만들어 넘기면 사라질 수 있고, 그래도 문진은 동작한다.
LAST_QUESTION_KEY = "_last_question"  # 직전에 보낸 next_question (짧은 대답 해석용)
CONDITION_KEY = "_condition"
DETAIL_ASKED_KEY = "_asked_detail_location"  # 넓은 부위의 상세 위치를 이미 물었는지
AGE_KEY = "_age"  # 나이 질문에 대한 환자 답 (그대로 저장)
BROAD_LOCATIONS = {"배": "배의 어느 쪽이 아프신가요? (명치, 오른쪽 위·아래, 왼쪽 위·아래, 배꼽 주변 등)",
                   "복부": "배의 어느 쪽이 아프신가요? (명치, 오른쪽 위·아래, 왼쪽 위·아래, 배꼽 주변 등)",
                   "머리": "머리의 어느 쪽이 아프신가요? (이마, 관자놀이, 뒷머리, 한쪽·양쪽 등)"}
# 나이에 따라 진료과(소아청소년과/내과 등)가 갈리는 증상
AGE_DEPENDENT_MARKERS = ("기침", "콧물", "코막힘", "가래", "발열", "열", "설사", "구토", "배", "복")
AGE_QUESTION = "환자분의 나이가 어떻게 되시나요? (어린이라면 나이를 알려 주세요)"  # 사용자가 말한 질환 {"name", "kcd_code", "departments", "certainty"}

# ── 증상 종류별 필수 슬롯 ──
# 통증이면 5개 모두, 전신 증상(기침·발열 등)은 부위·강도를 묻지 않고, 그 밖의 증상은 강도만 뺀다.
PAIN_MARKERS = ("통", "아프", "아파", "쓰리", "쑤시", "결리", "결려", "뻐근", "저리", "저림")
SYSTEMIC_MARKERS = (
    "기침", "콧물", "코막힘", "재채기", "가래", "발열", "열", "오한", "몸살", "피로", "피곤",
    "어지러", "어지럼", "불면", "잠", "설사", "구토", "메스꺼", "울렁", "식욕", "체중",
)

# ── 사용자가 질환명을 말했을 때 확신 정도 ──
# 추정 표현이 있으면 문진을 계속한다 (진단받았다는 표현보다 우선).
SUSPECTED_MARKERS = ("같아", "같은데", "같습니다", "같아요", "인가", "인지", "아닐까", "의심", "듯", "걸린것", "걸렸나")
DIAGNOSED_MARKERS = (
    "진단", "판정", "앓", "지병", "환자", "약먹", "약을먹", "복용", "치료중", "치료받", "다니",
    "관리", "있어서", "있는데", "있어요", "있습니다", "병원찾", "병원추천", "병원알려", "병원어디",
)
TOP_LEVEL_KEYS = (
    "action",
    "symptom_updates",
    "answered_fields",
    "next_question",
    "analysis",
    "emergency_guidance",
)
ANALYSIS_KEYS = (
    "assessment_status",
    "symptom_summary",
    "suspected_diseases",
    "recommended_department",
)
ALLOWED_STATUSES = {
    "PRELIMINARY",
    "INSUFFICIENT_INFORMATION",
    "INSUFFICIENT_EVIDENCE",
}
# 진료과 목록은 병원 API(hospital/dept_codes.py)와 같은 것을 쓴다 → 추천 진료과가 그대로 병원 검색에 쓰임

# ── 위험 신호(응급) 감지 ──
# 사용자 발화에 아래 표현이 있으면 문진·분석을 멈추고 응급 안내를 돌려준다.
# 키워드 기반 1차 안전장치다. 부정 표현("가슴은 안 아파요")도 응급으로 잡을 수 있는데,
# 응급을 놓치는 것보다 낫다고 보고 의도적으로 보수적으로 둔다. 목록은 팀·의료 자문 검토 대상.
EMERGENCY_GUIDANCE_DEFAULT = (
    "말씀하신 증상은 응급 상황일 수 있습니다. 지금 바로 119에 전화하거나 가까운 응급실을 방문하세요. "
    "이 서비스는 진단을 대신하지 않습니다."
)
EMERGENCY_GUIDANCE_SELF_HARM = (
    "지금 많이 힘드신 것 같습니다. 자살예방상담전화 109(24시간)나 119에 바로 연락해 도움을 받으세요."
)
EMERGENCY_RULES = (
    # (규칙 이름, 키워드 묶음들) — 모든 묶음이 맞아야 하고, 묶음 안에서는 하나만 있으면 된다
    ("흉통", (("흉통",),)),
    ("흉통", (("가슴",), ("통증", "아파", "아프", "조이", "조여", "짓눌", "쥐어짜", "뻐근"))),
    ("호흡곤란", (("호흡곤란", "호흡 곤란", "숨이 차", "숨쉬기 힘", "숨쉬기가 힘", "숨을 못", "숨이 막", "숨을 쉴 수"),)),
    ("의식저하", (("의식이 없", "의식을 잃", "의식이 흐", "기절", "실신", "쓰러졌", "쓰러져", "정신을 잃"),)),
    ("뇌졸중 의심", (("마비", "말이 어눌", "발음이 어눌", "입이 돌아", "얼굴이 한쪽", "한쪽 팔", "한쪽 다리", "한쪽 몸"),)),
    ("대량 출혈", (("피를 토", "토혈", "객혈", "각혈", "피가 멈추지", "피가 안 멈", "피를 많이"),)),
    ("경련", (("경련", "발작", "까무러"),)),
    ("극심한 두통", (("벼락", "생애 최악", "살면서 가장 심한", "갑자기 극심한"),)),
    ("아나필락시스 의심", (("아나필락시스",),)),
    ("아나필락시스 의심", (("목이 붓", "목이 부어", "입술이 붓", "혀가 붓"), ("숨", "호흡"))),
)
SELF_HARM_KEYWORDS = ("죽고 싶", "죽고싶", "자살", "자해", "목숨을 끊")


def detect_emergency(text):
    """위험 신호가 있으면 (규칙 이름, 안내 문구), 없으면 None."""
    msg = " ".join((text or "").split())
    if not msg:
        return None
    if any(k in msg for k in SELF_HARM_KEYWORDS):
        return "자해 위험", EMERGENCY_GUIDANCE_SELF_HARM
    for name, groups in EMERGENCY_RULES:
        if all(any(k in msg for k in group) for group in groups):
            return name, EMERGENCY_GUIDANCE_DEFAULT
    return None


def build_emergency_payload(guidance):
    payload = {
        "action": "EMERGENCY",
        "symptom_updates": {},
        "answered_fields": [],
        "next_question": None,
        "analysis": None,
        "emergency_guidance": guidance,
    }
    validate_backend_response(payload)
    return payload
FIELD_ALIASES = {
    "main_symptom": ("main_symptom", "symptom", "primary_symptom", "chief_complaint", "주증상"),
    "body_location": ("body_location", "location", "pain_location", "site", "area", "부위"),
    "onset": ("onset", "start_time", "started_at", "duration", "when", "발생시점", "발생_시점"),
    "severity": ("severity", "pain_level", "pain_score", "intensity", "강도", "통증강도"),
    "additional_symptoms": (
        "additional_symptoms",
        "other_symptoms",
        "accompanying_symptoms",
        "associated_symptoms",
        "동반증상",
        "동반_증상",
    ),
    "region": ("region", "address", "city", "district", "위치", "지역"),
}


def init_state():
    return {
        "main_symptom": None,
        "body_location": None,
        "onset": None,
        "severity": None,
        "additional_symptoms": None,
        "region": None,
    }


def _public_state(state):
    """LLM 프롬프트에 넣을 증상 상태 (서버 내부 키 제외)."""
    return {k: state.get(k) for k in ALLOWED_FIELDS}


def required_fields(state):
    """주증상 종류에 따라 이번 문진에서 꼭 채울 슬롯."""
    main = "".join(str(state.get("main_symptom") or "").split())
    if not main or any(k in main for k in PAIN_MARKERS):
        return CORE_SYMPTOM_FIELDS
    if any(k in main for k in SYSTEMIC_MARKERS):
        return ("main_symptom", "onset", "additional_symptoms")
    return ("main_symptom", "body_location", "onset", "additional_symptoms")


def condition_certainty(text):
    """질환명을 말한 문장이 '진단받음(diagnosed)'인지 '추정(suspected)'인지. 표현이 없으면 suspected."""
    msg = "".join((text or "").split())
    if any(k in msg for k in SUSPECTED_MARKERS):
        return "suspected"
    if any(k in msg for k in DIAGNOSED_MARKERS):
        return "diagnosed"
    return "suspected"


def _resolve_stated_condition(raw_condition, user_message):
    """LLM이 뽑은 질환명(stated_condition)과 사용자 문장을 질환 표에 맞춘다. 표에 없으면 None.

    표에 있는 질환만 쓰므로 진료과는 항상 대응표 기준이다.
    확신 정도: 사용자 문장에 추정 표현이 있으면 suspected가 우선하고, 그 밖에는 LLM 값 → 문장 표현 순.
    """
    raw_name, raw_certainty = None, None
    if isinstance(raw_condition, dict):
        raw_name = _normalize_str_or_none(raw_condition.get("name"))
        raw_certainty = raw_condition.get("certainty")
    elif isinstance(raw_condition, str):
        raw_name = _normalize_str_or_none(raw_condition)

    cond = find_condition(raw_name) or find_condition(user_message)
    if not cond:
        return None

    msg = "".join((user_message or "").split())
    if any(k in msg for k in SUSPECTED_MARKERS):
        certainty = "suspected"  # "감기 같아요"는 LLM이 diagnosed라고 해도 문진을 계속한다
    elif raw_certainty in ("diagnosed", "suspected"):
        certainty = raw_certainty
    else:
        certainty = condition_certainty(user_message)
    return {
        "name": cond["name"],
        "kcd_code": cond["kcd_code"],
        "departments": list(cond["departments"]),
        "certainty": certainty,
    }


def _is_condition_name_only(text, condition):
    """main_symptom에 증상 대신 질환명만 들어갔는지 ("감기", "감기 증상")."""
    s = "".join(str(text or "").split())
    if not s:
        return False
    found = find_condition(s)
    if not found or found["name"] != condition["name"]:
        return False
    for alias in found["aliases"]:
        if alias in s.lower() and len(s) - len(alias) <= 3:
            return True
    return False


def _strip_code_fences(text):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _extract_json_object(text):
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return {}
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(cleaned[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {}


def _non_ws_len(text):
    return len("".join(text.split()))


def _truncate_non_ws(text, max_non_ws):
    if _non_ws_len(text) <= max_non_ws:
        return text.strip()
    out = []
    count = 0
    for ch in text.strip():
        if not ch.isspace():
            if count >= max_non_ws:
                break
            count += 1
        out.append(ch)
    return "".join(out).strip()


def _normalize_str_or_none(val):
    if val is None or isinstance(val, (bool, dict, list)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("null", "none", "nan"):
        return None
    return s


def _has_hangul(text):
    return bool(re.search(r"[가-힣]", text or ""))


def _clean_korean_disease_name(name):
    s = _normalize_str_or_none(name)
    if not s:
        return None
    # "위궤양 (Peptic Ulcer)" -> "위궤양"
    cleaned = re.sub(r"\s*\([A-Za-z0-9\s,\-/'&.]+\)\s*", " ", s).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or s


def translate_titles_to_korean(titles):
    if not titles:
        return []
    prompt = f"""
아래 영문 의학 용어 및 질환명 목록을 자연스러운 한국어 병명이나 증상명으로 각각 번역해줘.
영어 표기나 괄호 병기 없이 순수 한국어만 입력 순서대로 JSON 문자열 배열로 출력해. 다른 설명은 절대 쓰지 마.

예시 입력: ["Abdominal Pain", "Peptic Ulcer", "Gastroenteritis"]
예시 출력: ["복통", "위궤양", "위장염"]

[입력 목록]
{json.dumps(titles, ensure_ascii=False)}
"""
    try:
        res = client.chat.completions.create(
            model="HCX-005",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = _strip_code_fences(res.choices[0].message.content or "[]")
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end + 1]
        translated = json.loads(raw)
        if isinstance(translated, list) and len(translated) == len(titles):
            result = []
            for orig, ko in zip(titles, translated):
                ko_clean = _clean_korean_disease_name(ko)
                result.append(ko_clean or orig)
            return result
    except Exception:
        pass
    return list(titles)


def _normalize_severity(val):
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if 0 <= val <= 10 else None
    if isinstance(val, float):
        iv = int(round(val))
        return iv if 0 <= iv <= 10 else None
    if isinstance(val, str):
        digits = "".join(ch for ch in val if ch.isdigit())
        if digits:
            iv = int(digits)
            if 0 <= iv <= 10:
                return iv
    return None


def _normalize_additional_symptoms(val):
    if val is None:
        return None
    none_markers = {"없음", "없어요", "없습니다", "동반 증상 없음", "특별한 동반 증상 없음", "없어"}
    if isinstance(val, list):
        items = []
        for item in val:
            s = _normalize_str_or_none(item)
            if s and s not in none_markers and s not in items:
                items.append(s)
        return items
    if isinstance(val, str):
        s = _normalize_str_or_none(val)
        if s is None:
            return None
        if s in none_markers:
            return []
        items = []
        for part in s.replace("/", ",").split(","):
            p = part.strip()
            if p and p not in none_markers and p not in items:
                items.append(p)
        return items
    return None


def _extract_raw_field(raw_updates, user_message, field, current_state, last_question=None):
    for alias in FIELD_ALIASES.get(field, (field,)):
        if alias in raw_updates and raw_updates[alias] is not None:
            return raw_updates[alias]

    # 넓은 부위("배")에 상세 위치를 물은 직후에는 부위를 다시 채울 수 있다
    refining = field == "body_location" and last_question in BROAD_LOCATIONS.values()
    if current_state.get(field) is not None and not refining:
        return None

    msg = (user_message or "").strip()
    if not msg:
        return None

    if field == "main_symptom":
        if any(k in msg for k in ("아파", "아프", "통증", "쓰리", "뻐근", "쑤시", "결려", "불편")):
            if any(k in msg for k in ("배", "복부", "명치", "아랫배", "윗배")):
                return "복통"
            if any(k in msg for k in ("머리", "이마", "관자놀이", "뒷목")):
                return "두통"
            if "손목" in msg:
                return "손목 통증"
            if "발목" in msg:
                return "발목 통증"
            # "목"만으로는 인후통(목구멍)인지 목 근육 통증인지 알 수 없어서 인후 표현이 있을 때만 정한다
            if any(k in msg for k in ("인후", "편도", "목구멍", "목이 따가", "목이 칼칼", "삼킬 때", "침 삼킬")):
                return "인후통"
            return msg
    if field == "body_location":
        location_map = [
            ("오른쪽 아랫배", ("오른쪽 아랫배", "우하복부", "우측 하복부", "우측 하단", "오른쪽 아래")),
            ("왼쪽 아랫배", ("왼쪽 아랫배", "좌하복부", "좌측 하복부", "좌측 하단", "왼쪽 아래")),
            ("오른쪽 윗배", ("오른쪽 윗배", "우상복부", "우측 상복부", "우측 상단", "오른쪽 위", "오른쪽 갈비뼈 아래")),
            ("왼쪽 윗배", ("왼쪽 윗배", "좌상복부", "좌측 상복부", "좌측 상단", "왼쪽 위", "왼쪽 갈비뼈 아래")),
            ("아랫배", ("아랫배", "하복부")),
            ("윗배", ("윗배", "상복부")),
            ("명치", ("명치",)),
            ("배", ("배", "복부")),
            ("이마", ("이마",)),
            ("머리", ("머리",)),
            ("손목", ("손목",)),
            ("발목", ("발목",)),
            ("목", ("목",)),
        ]
        for label, keys in location_map:
            if any(k in msg for k in keys):
                return label
    if field == "onset" and any(k in msg for k in ("어제", "오늘", "그제", "일주일", "며칠", "시간 전", "방금", "아침", "점심", "저녁", "밤")):
        return msg
    if field == "severity":
        # "7점", "10점 중 7점", "7/10" → 마지막에 나온 점수를 쓴다 (숫자를 이어 붙이지 않음)
        scores = re.findall(r"(\d{1,2})\s*점", msg) or re.findall(r"(\d{1,2})\s*/\s*10", msg)
        if scores:
            return int(scores[-1])
        # 직전 질문이 강도였으면 "7", "7 정도요" 같은 숫자만 있는 대답도 받는다
        if last_question and any(w in last_question for w in ("강도", "0에서 10", "몇 점")):
            bare = re.fullmatch(r"\s*(\d{1,2})\s*(정도|쯤)?\s*(요|이요|이에요|예요)?\s*[.!]?\s*", msg)
            if bare:
                return int(bare.group(1))
    if field == "additional_symptoms":
        if any(k in msg for k in ("없어요", "없음", "없습니다", "없어")):
            return []
        keyword_map = [
            ("발열", ("발열", "미열", "고열", "열이", "열나", "열도", "열감")),
            ("구토", ("구토", "토했", "토할")),
            ("메스꺼움", ("메스꺼", "울렁", "속이 안")),
            ("설사", ("설사",)),
            ("기침", ("기침",)),
            ("콧물", ("콧물",)),
            ("오한", ("오한", "으슬")),
            ("어지럼증", ("어지럽", "어지럼")),
            ("두통", ("머리", "두통")),
        ]
        main = current_state.get("main_symptom")
        found = [
            label for label, keys in keyword_map
            if any(k in msg for k in keys) and label != main  # 주증상을 동반 증상으로 중복 저장하지 않음
        ]
        if found:
            return found
    return None


def is_core_complete(state):
    return all(state.get(k) is not None for k in required_fields(state))


def has_sufficient_info(state):
    if not state.get("main_symptom"):
        return False
    filled_count = sum(1 for k in CORE_SYMPTOM_FIELDS if state.get(k) is not None)
    return filled_count >= 2


def _build_symptom_summary(state, status):
    confirmed = []
    if state.get("main_symptom"):
        confirmed.append(f"주증상({state['main_symptom']})")
    if state.get("body_location"):
        confirmed.append(f"부위({state['body_location']})")
    if state.get("onset"):
        confirmed.append(f"발생 시점({state['onset']})")
    if state.get("severity") is not None:
        confirmed.append(f"통증 강도({state['severity']}점)")
    if state.get("additional_symptoms") is not None:
        if len(state["additional_symptoms"]) == 0:
            confirmed.append("동반 증상(없음)")
        else:
            confirmed.append(f"동반 증상({', '.join(state['additional_symptoms'])})")

    # 이 증상에 필요 없는 슬롯(예: 기침의 통증 강도)은 '확인되지 않음'으로 쓰지 않는다
    missing = [FIELD_LABELS[k] for k in required_fields(state) if state.get(k) is None or state.get(k) == ""]

    if confirmed and missing:
        summary = f"{', '.join(confirmed)}은 확인됐으나 {', '.join(missing)}은 확인되지 않았습니다."
    elif confirmed:
        summary = f"{', '.join(confirmed)}이 확인되었습니다."
    else:
        summary = f"확인된 증상 정보가 부족합니다. ({', '.join(missing)} 미확인)"

    if status == "INSUFFICIENT_EVIDENCE":
        summary += " 다만 현재 정보만으로는 특정 질환을 뒷받침할 의학적 근거가 부족합니다."
    return _truncate_non_ws(summary, 2000)


def _infer_fallback_department(state):
    text = " ".join([
        str(state.get("main_symptom") or ""),
        str(state.get("body_location") or ""),
        " ".join(state.get("additional_symptoms") or []),
    ])
    if any(k in text for k in ("오른쪽 아랫배", "우하복부", "맹장", "충수")):
        return "외과"
    if any(k in text for k in ("배", "복부", "복통", "명치", "속", "위", "설사", "구토", "메스꺼")):
        return "내과"
    if any(k in text for k in ("목", "코", "귀", "인후", "편도", "콧물")):
        return "이비인후과"
    if any(k in text for k in ("머리", "이마", "두통", "어지럼", "편두통")):
        return "신경과"
    if any(k in text for k in ("눈", "시야", "충혈")):
        return "안과"
    if any(k in text for k in ("피부", "두드러기", "발진", "가려")):
        return "피부과"
    if any(k in text for k in ("관절", "무릎", "허리", "발목", "어깨", "뼈")):
        return "정형외과"
    if any(k in text for k in ("치아", "잇몸", "이빨", "충치")):
        return "치과"
    return "가정의학과"


def validate_backend_response(payload):
    if not isinstance(payload, dict) or set(payload.keys()) != set(TOP_LEVEL_KEYS):
        raise ValueError("최상위 5개 필드 규격 위반")

    action = payload["action"]
    if action not in ("ASK_FOLLOW_UP", "COMPLETE", "EMERGENCY"):
        raise ValueError("action 허용값 위반")

    guidance = payload["emergency_guidance"]
    if action == "EMERGENCY":
        if not isinstance(guidance, str) or _non_ws_len(guidance) == 0:
            raise ValueError("EMERGENCY 시 emergency_guidance 필요")
        if payload["next_question"] is not None or payload["analysis"] is not None:
            raise ValueError("EMERGENCY 시 next_question·analysis는 null이어야 함")
    elif guidance is not None:
        raise ValueError("EMERGENCY가 아니면 emergency_guidance는 null이어야 함")

    updates = payload["symptom_updates"]
    answered = payload["answered_fields"]
    if not isinstance(updates, dict) or not isinstance(answered, list):
        raise ValueError("symptom_updates 또는 answered_fields 타입 위반")

    for k, v in updates.items():
        if k not in ALLOWED_FIELDS:
            raise ValueError(f"허용되지 않은 symptom_updates 필드: {k}")
        if k == "severity" and v is not None:
            if not isinstance(v, int) or isinstance(v, bool) or not (0 <= v <= 10):
                raise ValueError("severity 타입/범위 위반")
        elif k == "additional_symptoms":
            if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                raise ValueError("additional_symptoms 타입 위반")
        else:
            if v is not None and not isinstance(v, str):
                raise ValueError(f"{k} 타입 위반")

    if len(answered) != len(set(answered)):
        raise ValueError("answered_fields 중복 위반")
    for f in answered:
        if f not in ALLOWED_FIELDS or f not in updates or updates[f] is None:
            raise ValueError(f"answered_fields 일관성 위반: {f}")

    next_q = payload["next_question"]
    analysis = payload["analysis"]

    if action == "EMERGENCY":
        return True
    if action == "ASK_FOLLOW_UP":
        if not isinstance(next_q, str) or not (1 <= _non_ws_len(next_q) <= 1000):
            raise ValueError("ASK_FOLLOW_UP 시 next_question 규격 위반")
        if analysis is not None:
            raise ValueError("ASK_FOLLOW_UP 시 analysis는 null이어야 함")
    else:
        if next_q is not None:
            raise ValueError("COMPLETE 시 next_question은 null이어야 함")
        if not isinstance(analysis, dict) or set(analysis.keys()) != set(ANALYSIS_KEYS):
            raise ValueError("analysis 내부 4개 필드 규격 위반")
        if analysis["assessment_status"] not in ALLOWED_STATUSES:
            raise ValueError("assessment_status 허용값 위반")
        summary = analysis["symptom_summary"]
        if not isinstance(summary, str) or not (1 <= _non_ws_len(summary) <= 2000):
            raise ValueError("symptom_summary 길이 규격 위반")
        diseases = analysis["suspected_diseases"]
        if not isinstance(diseases, list) or len(diseases) > 3:
            raise ValueError("suspected_diseases 개수 규격 위반")
        for d in diseases:
            if not isinstance(d, dict) or set(d.keys()) != {"disease_name", "reason"}:
                raise ValueError("suspected_diseases 항목 키 규격 위반")
            if not isinstance(d["disease_name"], str) or _non_ws_len(d["disease_name"]) == 0:
                raise ValueError("disease_name 비어 있음")
            if not isinstance(d["reason"], str) or _non_ws_len(d["reason"]) == 0:
                raise ValueError("reason 비어 있음")
        dept = analysis["recommended_department"]
        if dept is not None and (not isinstance(dept, str) or not (1 <= _non_ws_len(dept) <= 100)):
            raise ValueError("recommended_department 길이/타입 규격 위반")

    return True


def _pick_next_question(current_state, raw_question=None):
    required = required_fields(current_state)
    q = _normalize_str_or_none(raw_question)
    if q:
        # 이 증상에 필요 없는 슬롯을 묻는 질문은 버린다 (예: 기침에 통증 강도)
        if "severity" not in required and any(w in q for w in ("강도", "0에서 10", "몇 점")):
            q = None
        elif "body_location" not in required and any(w in q for w in ("어느 부위", "부위")):
            q = None
    if q:
        if current_state.get("main_symptom") and any(w in q for w in ("주된 증상", "어디가 어떻게 불편", "무엇인가요")):
            q = None
        elif current_state.get("onset") and any(w in q for w in ("언제", "시작", "얼마나 됐")):
            q = None
        elif current_state.get("body_location") and any(w in q for w in ("어느 부위", "어디가", "부위")):
            q = None
        elif current_state.get("severity") is not None and any(w in q for w in ("강도", "0에서 10", "몇 점")):
            q = None
        elif current_state.get("additional_symptoms") is not None and any(w in q for w in ("동반", "다른 증상")):
            q = None

    if q:
        return _truncate_non_ws(q, 1000)

    condition = current_state.get(CONDITION_KEY)
    if not current_state.get("main_symptom") and condition:
        return f"말씀하신 '{condition['name']}' 관련해서 지금 어떤 증상이 있으신가요? (예: 기침, 열, 통증 부위)"
    for field in required:
        if current_state.get(field) is None or current_state.get(field) == "":
            return FIELD_QUESTIONS[field]
    return FIELD_QUESTIONS["additional_symptoms"]


def _extra_question(state):
    """핵심 슬롯이 다 찼어도 진료과가 갈릴 수 있어 한 번 더 물을 질문 (없으면 None)."""
    loc = "".join(str(state.get("body_location") or "").split())
    if loc in BROAD_LOCATIONS and not state.get(DETAIL_ASKED_KEY):
        state[DETAIL_ASKED_KEY] = True
        return BROAD_LOCATIONS[loc]
    text = f"{state.get('main_symptom') or ''}{state.get('body_location') or ''}"
    if AGE_KEY not in state and any(m in text for m in AGE_DEPENDENT_MARKERS):
        state[AGE_KEY] = None
        return AGE_QUESTION
    return None


def _sanitize_analysis(raw_analysis, current_state, rag_context=None):
    if not isinstance(raw_analysis, dict):
        raw_analysis = {}

    sufficient = has_sufficient_info(current_state)
    docs = (rag_context or {}).get("medlineplus_docs", [])
    has_rag_docs = bool(docs)

    if not sufficient:
        status = "INSUFFICIENT_INFORMATION"
    elif rag_context is not None and not has_rag_docs:
        status = "INSUFFICIENT_EVIDENCE"
    else:
        status = raw_analysis.get("assessment_status")
        if status not in ALLOWED_STATUSES:
            status = "PRELIMINARY"

    summary = _normalize_str_or_none(raw_analysis.get("symptom_summary"))
    if not summary or (status == "INSUFFICIENT_INFORMATION" and "확인되지" not in summary and "부족" not in summary):
        summary = _build_symptom_summary(current_state, status)
    summary = _truncate_non_ws(summary, 2000)

    diseases = []
    if status == "PRELIMINARY":
        raw_diseases = raw_analysis.get("suspected_diseases")
        if isinstance(raw_diseases, list):
            for item in raw_diseases[:3]:
                if isinstance(item, dict):
                    d_name = _clean_korean_disease_name(item.get("disease_name"))
                    d_reason = _normalize_str_or_none(item.get("reason"))
                    if d_name and d_reason:
                        diseases.append({
                            "disease_name": d_name,
                            "reason": d_reason,
                        })
        # LLM이 근거 있는 질환을 내지 않았으면 빈 목록으로 둔다.
        # (예전에는 검색 문서 제목을 질환으로 채웠는데, 제목이 증상명이거나 검색이 빗나가면
        #  근거 없는 질환이 나가서 제거함)

        # 혹시 영어로만 남은 병명이 있으면 한국어로 한 번 더 번역
        en_indices = [i for i, d in enumerate(diseases) if not _has_hangul(d["disease_name"])]
        if en_indices:
            translated = translate_titles_to_korean([diseases[i]["disease_name"] for i in en_indices])
            for idx, ko_name in zip(en_indices, translated):
                diseases[idx]["disease_name"] = _clean_korean_disease_name(ko_name) or diseases[idx]["disease_name"]

    recommended_dept = None
    if status == "PRELIMINARY":
        # 분과·다른 표기는 병원 API 표준 진료과로 통일 (예: 소화기내과 → 내과). 목록 밖이면 버림
        recommended_dept = normalize_department(_normalize_str_or_none(raw_analysis.get("recommended_department")))
        if not recommended_dept:
            # LLM이 진료과를 못 고르면: 의심 질환 → 사용자가 의심한 질환 → 증상 키워드 순으로 대응표에서 찾는다
            for d in diseases:
                depts = departments_for_condition(d["disease_name"])
                if depts:
                    recommended_dept = depts[0]
                    break
        if not recommended_dept:
            condition = current_state.get(CONDITION_KEY)
            if condition and condition.get("certainty") == "suspected" and condition.get("departments"):
                recommended_dept = condition["departments"][0]
        if not recommended_dept:
            recommended_dept = _infer_fallback_department(current_state)

    return {
        "assessment_status": status,
        "symptom_summary": summary,
        "suspected_diseases": diseases,
        "recommended_department": recommended_dept,
    }


def build_condition_payload(condition, symptom_updates=None, answered_fields=None):
    """진단받은 질환을 말한 경우: 문진 없이 질환 대응표의 진료과로 바로 COMPLETE.

    프론트 계약(analysis 4개 필드)을 그대로 쓴다. 진료과 후보가 여러 개여도 계약상 대표 1개만 내보낸다.
    """
    name = condition["name"]
    summary = (
        f"말씀하신 진단명({name})을 기준으로 진료과를 안내합니다. "
        "증상 문진은 진행하지 않았으니, 새로 생기거나 심해진 증상이 있으면 알려 주세요."
    )
    reason = (
        f"사용자가 진단받았다고 말한 질환입니다. 이 서비스가 진단한 결과가 아니며, "
        f"진료과는 질병분류(KCD {condition['kcd_code']}) 기준 대응표로 안내합니다."
    )
    payload = {
        "action": "COMPLETE",
        "symptom_updates": symptom_updates or {},
        "answered_fields": answered_fields or [],
        "next_question": None,
        "analysis": {
            "assessment_status": "PRELIMINARY",
            "symptom_summary": summary,
            "suspected_diseases": [{"disease_name": name, "reason": reason}],
            "recommended_department": condition["departments"][0],
        },
        "emergency_guidance": None,
    }
    validate_backend_response(payload)
    return payload


def _sanitize_response(raw_obj, current_state, turn_count, max_turns=5, user_message="", last_question=None):
    if not isinstance(raw_obj, dict):
        raw_obj = {}

    raw_updates = raw_obj.get("symptom_updates")
    if not isinstance(raw_updates, dict):
        raw_updates = {}

    symptom_updates = {}
    answered_fields = []
    state_before = {f: current_state.get(f) for f in ALLOWED_FIELDS}
    had_main_symptom = bool(state_before["main_symptom"])

    for field in ALLOWED_FIELDS:
        raw_val = _extract_raw_field(raw_updates, user_message, field, current_state, last_question)
        if raw_val is None:
            continue

        if field == "severity":
            norm_val = _normalize_severity(raw_val)
            if norm_val is not None:
                symptom_updates[field] = norm_val
                current_state[field] = norm_val
                answered_fields.append(field)
        elif field == "additional_symptoms":
            norm_list = _normalize_additional_symptoms(raw_val)
            if norm_list is not None:
                existing = current_state.get("additional_symptoms")
                if isinstance(existing, list) and len(existing) > 0 and len(norm_list) > 0:
                    merged_list = list(existing)
                    for item in norm_list:
                        if item not in merged_list:
                            merged_list.append(item)
                    norm_list = merged_list
                symptom_updates[field] = norm_list
                current_state[field] = norm_list
                answered_fields.append(field)
        else:
            norm_str = _normalize_str_or_none(raw_val)
            if norm_str is not None:
                symptom_updates[field] = norm_str
                current_state[field] = norm_str
                answered_fields.append(field)

    # ── 사용자가 질환명을 말한 경우 ("고혈압 있어요", "감기 같아요") ──
    condition = _resolve_stated_condition(raw_obj.get("stated_condition"), user_message)
    if condition:
        # 질환명이 주증상 슬롯에 들어갔으면 비운다 (증상은 따로 묻는다)
        if "main_symptom" in symptom_updates and _is_condition_name_only(symptom_updates["main_symptom"], condition):
            del symptom_updates["main_symptom"]
            answered_fields.remove("main_symptom")
            current_state["main_symptom"] = None
        current_state[CONDITION_KEY] = condition

        # 진단받은 질환 + 아직 증상 없음(첫 요청) → 문진 없이 진료과 안내
        if condition["certainty"] == "diagnosed" and not had_main_symptom and not current_state.get("main_symptom"):
            # 같은 문장에서 채워진 슬롯(예: "10년 전 진단" → onset)은 증상이 아니므로 되돌린다
            for field in symptom_updates:
                if field != "region":
                    current_state[field] = state_before[field]
            kept = {k: v for k, v in symptom_updates.items() if k == "region"}
            current_state[LAST_QUESTION_KEY] = None
            return build_condition_payload(condition, kept, [k for k in answered_fields if k in kept])

    action = raw_obj.get("action")
    if action not in ("ASK_FOLLOW_UP", "COMPLETE"):
        action = "COMPLETE" if (turn_count >= max_turns or is_core_complete(current_state)) else "ASK_FOLLOW_UP"
    # 끝낼지는 서버가 정한다: 상한 도달 전에는 핵심 슬롯이 다 찰 때까지 묻는다 (LLM의 조기 COMPLETE 무시)
    extra_q = None
    if turn_count >= max_turns:
        action = "COMPLETE"
    elif not is_core_complete(current_state):
        action = "ASK_FOLLOW_UP"
    else:
        extra_q = _extra_question(current_state)
        action = "ASK_FOLLOW_UP" if extra_q else "COMPLETE"

    if action == "ASK_FOLLOW_UP":
        next_q = extra_q or _pick_next_question(current_state, raw_obj.get("next_question"))
        current_state[LAST_QUESTION_KEY] = next_q
        payload = {
            "action": "ASK_FOLLOW_UP",
            "symptom_updates": symptom_updates,
            "answered_fields": answered_fields,
            "next_question": next_q,
            "analysis": None,
            "emergency_guidance": None,
        }
        validate_backend_response(payload)
        return payload

    current_state[LAST_QUESTION_KEY] = None
    analysis = _sanitize_analysis(raw_obj.get("analysis"), current_state, rag_context=None)
    payload = {
        "action": "COMPLETE",
        "symptom_updates": symptom_updates,
        "answered_fields": answered_fields,
        "next_question": None,
        "analysis": analysis,
        "emergency_guidance": None,
    }
    validate_backend_response(payload)
    return payload


def run_triage_turn(user_message, current_state, turn_count, max_turns=5, last_question=None):
    # 위험 신호는 LLM 호출 전에 먼저 본다 (응급 안내가 최우선)
    emergency = detect_emergency(user_message)
    if emergency:
        return build_emergency_payload(emergency[1])
    # "뇌졸중 온 것 같아요"처럼 응급 질환을 의심하는 말도 LLM 없이 응급 안내 (진단받았다는 말은 제외)
    stated = find_condition(user_message)
    if stated and stated["emergency_if_suspected"] and condition_certainty(user_message) != "diagnosed":
        return build_emergency_payload(EMERGENCY_GUIDANCE_DEFAULT)

    # 직전 질문: 인자 > 서버가 state에 저장한 값. "7", "네" 같은 짧은 대답을 해석하는 데 쓴다
    last_question = _normalize_str_or_none(last_question) or _normalize_str_or_none(current_state.get(LAST_QUESTION_KEY))
    if last_question == AGE_QUESTION:
        current_state[AGE_KEY] = _truncate_non_ws(user_message.strip(), 30)
    required = required_fields(current_state)
    known_condition = current_state.get(CONDITION_KEY)

    system_prompt = f"""
너는 환자 증상을 문진하는 의료 안내 AI야.
반드시 아래 동일한 JSON 응답 규격만 출력하고, JSON 외의 설명이나 Markdown 코드 블록은 절대 출력하지 마.

[대화 규칙]
- 현재 턴: {turn_count} / 최대 턴: {max_turns}
- 위치(region)는 시스템이 자동으로 파악하므로 환자에게 주소를 절대 묻지 마. (환자가 먼저 지역을 말한 경우에만 symptom_updates.region에 반영해.)
- 환자 한 마디에 여러 정보가 있으면 모두 symptom_updates에 넣어. 예: "어제부터 오른쪽 윗배가 아파요" → main_symptom "복통", body_location "오른쪽 윗배", onset "어제". 이미 채워진 항목은 다시 묻지 마.
- 직전 질문이 있으면 환자의 짧은 대답("7", "네", "아니요")은 그 질문에 대한 답으로 해석해.
- 현재 필수 수집 항목: {", ".join(required)}. (주증상이 통증이 아니면 severity, 기침·발열 같은 전신 증상이면 body_location도 묻지 마.)
- 아직 수집되지 않은 필수 항목이 남아 있고 현재 턴이 최대 턴 미만이면 action을 "ASK_FOLLOW_UP"으로 하고, 미확인 항목 중 가장 중요한 1가지만 next_question으로 질문해.
- 필수 항목이 모두 수집되었거나 현재 턴이 최대 턴에 도달하면 action을 "COMPLETE"로 설정해.
- 환자가 질환명을 말하면(예: "고혈압 있어요", "감기 같아요") stated_condition에 넣어. 질환명은 main_symptom에 넣지 말고, 실제 증상만 main_symptom에 넣어.
  - certainty: 진단받았거나 앓고 있는 질환이면 "diagnosed", 스스로 추측하는 것이면 "suspected".
{f"- 이전에 환자가 말한 질환: {known_condition['name']} ({known_condition['certainty']})" if known_condition else ""}

[현재까지 누적된 증상 상태]
{json.dumps(_public_state(current_state), ensure_ascii=False)}

[필드 규격]
{{
  "action": "ASK_FOLLOW_UP 또는 COMPLETE",
  "symptom_updates": {{}},
  "answered_fields": [],
  "next_question": null,
  "analysis": null,
  "stated_condition": null
}}

0. stated_condition: 환자가 이번에 질환명을 말했으면 {{"name": "질환명", "certainty": "diagnosed 또는 suspected"}}, 아니면 null.
1. symptom_updates: 이번 사용자 답변에서 확인하거나 정정한 필드만 포함. 갱신할 내용이 없으면 {{}}.
   - main_symptom: string | null
   - body_location: string | null
   - onset: string | null
   - severity: integer(0~10) | null
   - additional_symptoms: array[string] (전체 목록 교체, 동반 증상 없음 확인 시 [])
   - region: string | null
2. answered_fields: 이번 응답에서 확인한 필드명 배열(중복 불가, symptom_updates에 존재하는 non-null 필드만). 없으면 [].
3. next_question: action이 "ASK_FOLLOW_UP"이면 질문 1개, "COMPLETE"이면 null.
4. analysis: action이 "ASK_FOLLOW_UP"이면 null. "COMPLETE"이면 assessment_status, symptom_summary, suspected_diseases, recommended_department 4개 필드 포함.
5. 정의되지 않은 필드(session_id, turn_count, code, message, data 등)는 절대 생성하지 마.
"""

    messages = [{"role": "system", "content": system_prompt}]
    if last_question:
        messages.append({"role": "assistant", "content": last_question})
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="HCX-005",
            messages=messages,
            temperature=0.1,
        )
        raw_content = response.choices[0].message.content or ""
    except Exception:
        raw_content = ""

    parsed = _extract_json_object(raw_content)
    return _sanitize_response(
        parsed,
        current_state=current_state,
        turn_count=turn_count,
        max_turns=max_turns,
        user_message=user_message,
        last_question=last_question,
    )


def _condition_prompt_line(state):
    condition = state.get(CONDITION_KEY)
    if not condition:
        return ""
    if condition["certainty"] == "diagnosed":
        return f"[환자가 진단받은 기저질환] {condition['name']} (현재 증상의 원인이라고 단정하지 마)"
    return f"[환자가 스스로 의심한 질환] {condition['name']} (확진 아님. 증상과 문서로 확인될 때만 의심 질환에 넣어)"


def generate_final_analysis(current_state, rag_context):
    docs = (rag_context or {}).get("medlineplus_docs", [])
    if not has_sufficient_info(current_state):
        return _sanitize_analysis({}, current_state, rag_context=rag_context)
    if not docs:
        return _sanitize_analysis(
            {"assessment_status": "INSUFFICIENT_EVIDENCE"},
            current_state,
            rag_context=rag_context,
        )

    compact_docs = [
        {
            "title_ko": d.get("title_ko") or d["title"],
            "title_en": d["title"],
            "summary": d["summary"][:450],
        }
        for d in docs[:3]
    ]

    prompt = f"""
너는 미국 국립의학도서관(MedlinePlus.gov) 공식 문서를 근거로 환자 증상을 한국어로 정리하는 의료 안내 AI야.
반드시 아래 4개 키만 가진 순수 JSON 객체만 출력하고, 설명이나 코드블록은 절대 붙이지 마.

[환자 증상 정보]
{json.dumps(_public_state(current_state), ensure_ascii=False)}
{_condition_prompt_line(current_state)}
환자 나이: {current_state.get(AGE_KEY) or "모름 (성인 기준으로 진료과를 추천해)"}

[MedlinePlus.gov 공식 문서]
{json.dumps(compact_docs, ensure_ascii=False)}

[출력 JSON 형식]
{{
  "assessment_status": "PRELIMINARY",
  "symptom_summary": "환자의 주증상, 부위, 발생 시점, 강도, 동반 증상을 자연스러운 한국어 1~2문장으로 요약",
  "suspected_diseases": [
    {{
      "disease_name": "순수 한국어 질환명만 작성 (영문명이나 괄호 영문 병기 절대 금지. 예: 위궤양, 장염, 급성 충수염)",
      "reason": "환자가 실제로 말한 증상과 이 질환의 연관성, 그리고 확진이 아니라는 불확실성을 자연스러운 한국어로 설명"
    }}
  ],
  "recommended_department": "아래 표준 진료과 목록 중 가장 적합한 1개만 정확히 선택"
}}

[표준 진료과 목록]
{", ".join(STANDARD_DEPARTMENTS)}

[주의사항]
- suspected_diseases는 위 MedlinePlus.gov 문서 중 환자 증상과 실제로 관련 있는 항목만 최대 3개 작성해.
- disease_name에는 절대로 영어 단어나 괄호 안 영문명을 넣지 말고 한국어 병명만 적어.
- 모든 설명은 자연스러운 한국어로 작성해.
- reason에서 환자에 대한 사실은 [환자 증상 정보]에 있는 내용만 써. 환자가 말하지 않은 특징(예: 공복·야간 통증, 출혈, 발열, 구토)을 환자에게 있는 것처럼 쓰지 마.
- 문서에만 있는 질환 특징은 "~한 경우가 많으니 해당되는지 확인이 필요합니다"처럼 확인할 거리로만 써.
- 환자가 말한 증상과 직접 연결되는 근거가 없는 질환은 suspected_diseases에 넣지 마.
- 위 문서들이 환자 증상과 전혀 맞지 않아 근거가 부족하면 assessment_status를 "INSUFFICIENT_EVIDENCE", suspected_diseases를 [], recommended_department를 null로 반환해.
"""

    try:
        response = client.chat.completions.create(
            model="HCX-005",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw_content = response.choices[0].message.content or ""
    except Exception:
        raw_content = ""

    parsed = _extract_json_object(raw_content)
    if not parsed:
        # LLM 호출·파싱 실패: 근거를 확인하지 못했으므로 질환·진료과를 추정하지 않는다
        parsed = {"assessment_status": "INSUFFICIENT_EVIDENCE"}
    return _sanitize_analysis(parsed, current_state, rag_context=rag_context)