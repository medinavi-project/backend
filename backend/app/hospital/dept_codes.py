from typing import Optional

# 국립중앙의료원_전국 병·의원 찾기 서비스(B552657/HsptlAsembySearchService) 진료과목 코드(QD).
# 2026-11-21 실제 API(서울 강남구)로 코드별 조회 결과를 확인함 (예: D007 → 흉부외과의원, D026 → 치과의원).
# 병원 API에는 분과 코드가 없으므로 진료과는 이 목록의 이름으로만 사용한다.
DEPT_TO_QD: dict[str, str] = {
    "내과": "D001",
    "소아청소년과": "D002",
    "신경과": "D003",
    "정신건강의학과": "D004",
    "피부과": "D005",
    "외과": "D006",
    "흉부외과": "D007",
    "정형외과": "D008",
    "신경외과": "D009",
    "성형외과": "D010",
    "산부인과": "D011",
    "안과": "D012",
    "이비인후과": "D013",
    "비뇨기과": "D014",
    "재활의학과": "D016",
    "마취통증의학과": "D017",
    "가정의학과": "D022",
    "응급의학과": "D024",
    "치과": "D026",
}

STANDARD_DEPARTMENTS: tuple[str, ...] = tuple(DEPT_TO_QD)

# 분과·다른 표기 → 병원 API 표준 진료과.
# 내과 분과는 내과, 외과 분과는 외과로 통일한다.
# 신경과/신경외과, 정형외과, 소아청소년과, 정신건강의학과는 별도 전문과목이므로 합치지 않는다.
_EXTRA_ALIASES: dict[str, str] = {
    # 내과 분과
    "감염내과": "내과",
    "순환기내과": "내과",
    "심장내과": "내과",
    "소화기내과": "내과",
    "호흡기내과": "내과",
    "내분비내과": "내과",
    "내분비대사내과": "내과",
    "신장내과": "내과",
    "혈액종양내과": "내과",
    "종양내과": "내과",
    "류마티스내과": "내과",
    "알레르기내과": "내과",
    "노년내과": "내과",
    # 외과 분과
    "대장항문외과": "외과",
    "유방외과": "외과",
    "갑상선외과": "외과",
    "간담췌외과": "외과",
    "위장관외과": "외과",
    "혈관외과": "외과",
    "이식외과": "외과",
    "소아외과": "외과",
    # 다른 표기
    "심장혈관흉부외과": "흉부외과",
    "흉부심장혈관외과": "흉부외과",
    "비뇨의학과": "비뇨기과",
    "소아과": "소아청소년과",
    "정신과": "정신건강의학과",
    "신경정신과": "정신건강의학과",
    "응급실": "응급의학과",
    "치과의원": "치과",
}

ALIAS_TO_STANDARD: dict[str, str] = {name: name for name in DEPT_TO_QD}
ALIAS_TO_STANDARD.update(_EXTRA_ALIASES)


def normalize_department(department: Optional[str]) -> Optional[str]:
    """진료과 이름(분과·다른 표기 포함)을 병원 API 표준 진료과로 바꾼다. 모르면 None."""
    cleaned = "".join((department or "").split())
    if not cleaned:
        return None
    if cleaned in ALIAS_TO_STANDARD:
        return ALIAS_TO_STANDARD[cleaned]
    # "소화기내과(위장)" 같은 긴 표기: 가장 긴 별칭부터 부분 일치
    for alias in sorted(ALIAS_TO_STANDARD, key=len, reverse=True):
        if alias in cleaned:
            return ALIAS_TO_STANDARD[alias]
    return None


def resolve_department(department: str) -> Optional[tuple[str, str]]:
    standard = normalize_department(department)
    if not standard:
        return None
    return standard, DEPT_TO_QD[standard]
