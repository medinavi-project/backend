"""의약품 후보 검색. 후보는 반드시 사용자 확인이 필요하며 점수로 확정하지 않는다."""

import re
import unicodedata
import json
from functools import lru_cache
from pathlib import Path
from decimal import Decimal, InvalidOperation

from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein
from .mfds_api import MFDSError

UNIT = r"(?:밀리그램|밀리그람|mg|마이크로그램|마이크로그람|mcg|μg|ug|그램|그람|g|밀리리터|ml|%)"
STRENGTH_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*\s*" + UNIT + r")", re.I
)
FORM_RE = re.compile(
    r"(서방정|츄어블정|필름코팅정|장용정|캡슐|카플렛|과립|시럽|현탁액|주사액|점안액|연고|크림|패치|정|산|액|고)"
)


def normalize(value):
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = re.sub(r"밀리그램|밀리그람", "mg", text)
    text = re.sub(r"마이크로그램|마이크로그람|μg|ug", "mcg", text)
    text = re.sub(r"밀리리터", "ml", text)
    text = re.sub(r"그램|그람", "g", text)
    return re.sub(r"[^0-9a-z가-힣/%\.]", "", text)


def strengths(value):
    # 괄호 안의 성분명은 제품명 함량과 혼동하지 않는다.
    main = re.split(r"[（(]", unicodedata.normalize("NFKC", value or ""), maxsplit=1)[0]
    return [normalize(m.group(1)) for m in STRENGTH_RE.finditer(main)]


def compare_display_strengths(first, second):
    """제품명에 적힌 단일 질량만 비교하며 복합 함량 환산은 보류한다."""
    if not first or not second or len(first) != 1 or len(second) != 1:
        return None

    def mass(text):
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(mcg|mg|g)", normalize(text))
        if not m:
            return None
        try:
            return (
                Decimal(m.group(1))
                * {"mcg": Decimal(1), "mg": Decimal(1000), "g": Decimal(1000000)}[
                    m.group(2)
                ]
            )
        except InvalidOperation:
            return None

    a, b = mass(first[0]), mass(second[0])
    return None if a is None or b is None else a == b


def form(value):
    main = re.split(r"[（(]", unicodedata.normalize("NFKC", value or ""), maxsplit=1)[0]
    # 제형처럼 쓰이는 접미어만 인식한다.
    stem = re.sub(r"\d.*$", "", main).strip()
    matches = [m.group() for m in FORM_RE.finditer(stem)]
    return matches[-1] if matches and stem.endswith(matches[-1]) else None


@lru_cache(maxsize=None)
def _name_stem(value):
    main = re.split(r"[（(]", value or "", maxsplit=1)[0]
    main = re.sub(r"\d.*$", "", main)
    return re.sub(
        r"(?:서방정|츄어블정|필름코팅정|장용정|캡슐|카플렛|과립|시럽|현탁액|주사액|점안액|연고|크림|패치|정|산|액|고)$",
        "",
        normalize(main),
    )


DICTIONARY = (
    Path(__file__).parent / "data" / "mfds_collection" / "drug_name_dictionary.json"
)


@lru_cache(maxsize=4)
def _index(path, _mtime):
    """(rows, stems, coverage) for one dictionary file; cached per file version,
    so re-exporting the dictionary reloads it."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = [
            r
            for r in data["items"]
            if isinstance(r, dict)
            and re.fullmatch(r"\d{9}", str(r.get("item_seq", "")))
            and isinstance(r.get("official_name"), str)
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    stems = [_name_stem(r["official_name"]) for r in rows]
    return rows, stems, data.get("coverage", {})


def find_local_candidates(query, *, limit=5, dictionary_path=None):
    """약 이름 사전에서 후보만 찾는다. 현재 허가나 품목 확정은 하지 않는다."""
    path = Path(dictionary_path) if dictionary_path is not None else DICTIONARY
    result = {
        "status": "dictionary_unavailable",
        "coverage": "partial",
        "candidates": [],
        "truncated": False,
    }
    try:
        index = _index(str(path), path.stat().st_mtime)
    except OSError:
        index = None
    if index is None:
        return result
    rows, stems, result["collected_coverage"] = index
    stem = _name_stem(query)
    if len(stem) < 2:
        result["status"] = "query_too_short"
        return result
    qstrength, qform = strengths(query), form(query)
    found = []
    # C-level prefilter; 66 is the lowest ratio a one-edit match of length 3 can have.
    hits = process.extract(stem, stems, scorer=fuzz.ratio, score_cutoff=66, limit=None)
    for _, _, i in hits:
        row, other = rows[i], stems[i]
        name = row["official_name"]
        score = round(fuzz.ratio(stem, other), 1)
        # 짧은 이름은 한 글자 차이도 영향이 커 자동 유사 매칭을 보수적으로 제한한다.
        one_edit = (
            min(len(stem), len(other)) >= 3 and Levenshtein.distance(stem, other) <= 1
        )
        if not other or (
            not one_edit and score < (90 if min(len(stem), len(other)) < 3 else 80)
        ):
            continue
        sm = compare_display_strengths(qstrength, strengths(name))
        fm = None if not qform or not form(name) else qform == form(name)
        found.append(
            {
                **row,
                "similarity": score,
                "strength_match": sm,
                "form_match": fm,
                "identity_status": "confirmation_required",
                "current_official_verified": False,
            }
        )
    found.sort(
        key=lambda x: (
            x["strength_match"] is False,
            x["form_match"] is False,
            -x["similarity"],
            x["item_seq"],
        )
    )
    result.update(
        status="candidates_found" if found else "no_match_in_partial_dictionary",
        candidates=found[:limit],
        truncated=len(found) > limit,
    )
    return result


def query_variants(value):
    original = unicodedata.normalize("NFKC", value).strip()
    cleaned = re.sub(r"[^0-9a-zA-Z가-힣/%.]", "", original)
    canonical = normalize(original)
    # 제품명 앞부분으로 검색해 함량 표기 차이 및 OCR 오인식에 대비.
    base = re.split(r"\d|[（(]", cleaned, maxsplit=1)[0]
    variants = [original, cleaned, canonical, base]
    if len(base) >= 4:
        variants.append(base[:-1])
        # 앞 글자 또는 중간 글자 오인식에 대비한 제한적 부분 문자열이다.
        variants.append(base[1:])
        variants.append(base[:2])
    result = []
    for item in variants:
        if item and item not in result:
            result.append(item)
    return result[:7]


def search_drug(client, query, *, max_pages=2, page_size=50, max_candidates=20):
    if not query or not query.strip() or not normalize(query):
        raise ValueError("검색할 의약품명을 입력하세요.")
    if len(normalize(query)) < 2:
        return {
            "query": query,
            "status": "query_too_short",
            "message": "두 글자 이상 확인이 필요합니다.",
            "input_strength": [],
            "input_form": None,
            "candidate_count": 0,
            "truncated": False,
            "attempts": [],
            "errors": [],
            "candidates": [],
            "local_dictionary": find_local_candidates(query),
        }
    input_strength = strengths(query)
    input_form = form(query)
    seen, attempts, errors, truncated = {}, [], [], False
    for term in query_variants(query):
        term_count = 0
        try:
            for page in client.pages(
                client.permit_list, name=term, page_size=page_size, max_pages=max_pages
            ):
                term_count = page.total_count
                truncated |= (
                    page.page * page.page_size < page.total_count
                    and page.page == max_pages
                )
                for item in page.items:
                    seq, name = (
                        str(item.get("ITEM_SEQ") or "").strip(),
                        item.get("ITEM_NAME"),
                    )
                    if seq and name:
                        seen.setdefault(seq, item)
                    elif not any(
                        error["category"] == "data_quality" for error in errors
                    ):
                        errors.append({"category": "data_quality", "code": None})
            attempts.append(
                {
                    "query": term,
                    "status": "ok" if term_count else "no_results",
                    "total_count": term_count,
                }
            )
        except MFDSError as exc:
            errors.append({"category": exc.category, "code": exc.code})
            attempts.append(
                {"query": term, "status": exc.category, "total_count": None}
            )
            break
    local = find_local_candidates(query, limit=3)
    truncated |= local["truncated"]
    # 사전은 오래됐을 수 있으므로 최대 3개 코드만 현행 API로 재확인한다.
    if not errors:
        for hint in local["candidates"]:
            if hint["item_seq"] in seen:
                continue
            try:
                for page in client.pages(
                    client.permit_list,
                    item_seq=hint["item_seq"],
                    page_size=10,
                    max_pages=1,
                ):
                    for item in page.items:
                        if str(item.get("ITEM_SEQ", "")).strip() == hint[
                            "item_seq"
                        ] and item.get("ITEM_NAME"):
                            seen.setdefault(hint["item_seq"], item)
            except MFDSError as exc:
                errors.append({"category": exc.category, "code": exc.code})
                break
    candidates = []
    for seq, item in seen.items():
        name = item["ITEM_NAME"]
        item_strength = strengths(name)
        candidate_form = form(name)
        strength_match = compare_display_strengths(input_strength, item_strength)
        form_match = (
            None
            if not input_form or not candidate_form
            else input_form == candidate_form
        )
        score = round(
            fuzz.ratio(normalize(query), normalize(re.split(r"[（(]", name)[0])), 1
        )
        candidates.append(
            {
                "item_seq": seq,
                "official_name": name,
                "manufacturer": item.get("ENTP_NAME"),
                "strength_in_name": item_strength,
                "form_in_name": candidate_form,
                "classification": item.get("SPCLTY_PBLC"),
                "permit_status": item.get("CANCEL_NAME"),
                "strength_match": strength_match,
                "form_match": form_match,
                "similarity": score,
            }
        )
    candidates.sort(
        key=lambda x: (
            x["strength_match"] is False,
            x["form_match"] is False,
            -x["similarity"],
        )
    )
    if errors:
        status = "partial_error" if candidates else errors[0]["category"]
    else:
        status = "confirmation_required" if candidates else "no_results"
    return {
        "query": query,
        "status": status,
        "message": "후보의 제품명·함량·제형과 품목기준코드를 확인하세요."
        if candidates
        else ("조회 실패" if errors else "조회된 후보가 없습니다."),
        "input_strength": input_strength,
        "input_form": input_form,
        "candidate_count": len(candidates),
        "truncated": truncated or len(candidates) > max_candidates,
        "attempts": attempts,
        "errors": errors,
        "candidates": candidates[:max_candidates],
        "local_dictionary": local,
    }
