"""질환·콘텐츠 → 진료과 후보 대응표.

- kcd_departments.csv: KCD(한국표준질병사인분류) 대분류·세부범위 → 진료과 후보.
  KCD 범위와 이름은 공식 분류이고, 진료과 후보는 팀 기준이다 (공식 대응표는 없음).
- kdca_contents.csv: 질병관리청 국가건강정보포털 콘텐츠(cntntsSn) → KCD 코드, 진료과 후보, 응급 여부.
  department_basis가 "KCD 대분류"인 행은 kcd_departments.csv에서 나온 값과 같아야 한다.
- conditions.csv: 사용자가 말하는 질환명(별칭 포함) → KCD 코드 → 진료과 후보.
  departments가 비어 있으면 kcd_departments.csv 값을 쓰고, 채워져 있으면 그 값(팀 판단)을 쓴다.
  KCD 코드는 대표 코드이며 팀 확인 전이다.

진료과 이름은 병원 API 표준(hospital/dept_codes.py)만 쓴다. 분과는 내과·외과 등으로 통일한다.
"""

import csv
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.hospital.dept_codes import DEPT_TO_QD

_DIR = Path(__file__).resolve().parent
KCD_CSV = _DIR / "kcd_departments.csv"
KDCA_CSV = _DIR / "kdca_contents.csv"
CONDITIONS_CSV = _DIR / "conditions.csv"


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split("|") if v.strip()]


def _kcd_category(code: str) -> str:
    """'R04.2' → 'R04' (알파벳 1자 + 숫자 2자리로 범위 비교)."""
    return (code or "").strip().upper().replace(".", "")[:3]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _check_departments(row: dict[str, str], where: str) -> None:
    depts = _split(row["departments"])
    qds = _split(row["qd_codes"])
    unknown = [d for d in depts if d not in DEPT_TO_QD]
    if unknown:
        raise ValueError(f"{where}: 병원 API에 없는 진료과 {unknown}")
    if qds != [DEPT_TO_QD[d] for d in depts]:
        raise ValueError(f"{where}: departments와 qd_codes가 맞지 않음")


@lru_cache(maxsize=1)
def load_kcd_table() -> tuple[dict[str, str], ...]:
    rows = _read_csv(KCD_CSV)
    for row in rows:
        _check_departments(row, f"kcd {row['code_start']}-{row['code_end']}")
    return tuple(rows)


@lru_cache(maxsize=1)
def load_kdca_contents() -> dict[str, dict[str, str]]:
    rows = _read_csv(KDCA_CSV)
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        sn = row["cntnts_sn"].strip()
        if sn in out:
            raise ValueError(f"kdca {sn}: 콘텐츠 번호 중복")
        _check_departments(row, f"kdca {sn}")
        out[sn] = row
    return out


def departments_for_kcd(code: str) -> list[str]:
    """KCD 코드가 속한 가장 좁은 범위의 진료과 후보. 범위에 없으면 []."""
    cat = _kcd_category(code)
    if len(cat) < 3:
        return []
    best: Optional[dict[str, str]] = None
    best_span: Optional[tuple[str, str]] = None
    for row in load_kcd_table():
        start, end = row["code_start"], row["code_end"]
        if start <= cat <= end:
            # 더 좁은 범위(세부범위)를 우선한다
            if best is None or (start >= best_span[0] and end <= best_span[1]):
                best, best_span = row, (start, end)
    return _split(best["departments"]) if best else []


def content_departments(cntnts_sn) -> list[tuple[str, str]]:
    """콘텐츠의 진료과 후보 [(진료과, QD 코드), ...]. 앞쪽이 대표. 없는 콘텐츠면 []."""
    row = load_kdca_contents().get(str(cntnts_sn).strip())
    if not row:
        return []
    return [(d, DEPT_TO_QD[d]) for d in _split(row["departments"])]


def content_emergency(cntnts_sn) -> str:
    """'Y' / 'N' / '조건부'. 없는 콘텐츠면 'N'."""
    row = load_kdca_contents().get(str(cntnts_sn).strip())
    return (row or {}).get("emergency", "N") or "N"


def rank_departments(content_ids) -> list[str]:
    """검색된 콘텐츠들의 진료과 후보를 합쳐 순위를 매긴다.

    여러 문서에 공통으로 나온 진료과 → 문서 안에서 앞쪽(대표)일수록 우선.
    """
    score: dict[str, float] = {}
    for sn in content_ids:
        for idx, (dept, _qd) in enumerate(content_departments(sn)):
            score[dept] = score.get(dept, 0.0) + 1.0 + 1.0 / (idx + 2)
    return sorted(score, key=lambda d: (-score[d], d))


def _compact(text: str) -> str:
    return "".join((text or "").split()).lower()


@lru_cache(maxsize=1)
def load_conditions() -> tuple[dict, ...]:
    """질환명 표. 각 항목: name, aliases(이름 포함, 공백 제거), kcd_code, departments, emergency_if_suspected."""
    out = []
    seen: dict[str, str] = {}
    for row in _read_csv(CONDITIONS_CSV):
        name = row["name"].strip()
        override = _split(row["departments"])
        unknown = [d for d in override if d not in DEPT_TO_QD]
        if unknown:
            raise ValueError(f"condition {name}: 병원 API에 없는 진료과 {unknown}")
        depts = override or departments_for_kcd(row["kcd_code"])
        if not depts:
            raise ValueError(f"condition {name}: 진료과 후보 없음 (KCD {row['kcd_code']})")
        aliases = []
        for alias in [name, *_split(row["aliases"])]:
            key = _compact(alias)
            if key in seen and seen[key] != name:
                raise ValueError(f"condition {name}: 별칭 '{alias}'가 {seen[key]}와 겹침")
            seen[key] = name
            if key not in aliases:
                aliases.append(key)
        out.append({
            "name": name,
            "aliases": tuple(aliases),
            "kcd_code": row["kcd_code"].strip(),
            "departments": tuple(depts),
            "emergency_if_suspected": row["emergency_if_suspected"].strip().upper() == "Y",
        })
    return tuple(out)


@lru_cache(maxsize=1)
def _alias_index() -> tuple[tuple[str, dict], ...]:
    # 긴 별칭부터 맞춘다 ("류마티스관절염"이 "관절염"보다 먼저)
    pairs = [(alias, cond) for cond in load_conditions() for alias in cond["aliases"]]
    return tuple(sorted(pairs, key=lambda p: -len(p[0])))


def find_condition(text: Optional[str]) -> Optional[dict]:
    """문장 안에 표에 있는 질환명(별칭)이 있으면 그 질환. 공백은 무시한다. 없으면 None."""
    msg = _compact(text or "")
    if not msg:
        return None
    for alias, cond in _alias_index():
        if alias in msg:
            return cond
    return None


def departments_for_condition(name: Optional[str]) -> list[str]:
    """질환명 → 진료과 후보 (앞쪽이 대표). 모르는 질환이면 []."""
    cond = find_condition(name)
    return list(cond["departments"]) if cond else []
