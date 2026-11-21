"""Separate product identity from generated-answer citation validation."""

import re

from rapidfuzz import fuzz

from app.medicare_final.drug_search import form, normalize, strengths


def product_name(name):
    return re.split(r"[（(]", name, maxsplit=1)[0]


def select_candidate(query, found):
    if found.get("errors") or found.get("truncated"):
        return None
    candidates = [c for c in found["candidates"] if c.get("permit_status") == "정상"]
    exact = [
        c
        for c in candidates
        if normalize(product_name(c["official_name"])) == normalize(product_name(query))
    ]
    if len(exact) == 1:
        return exact[0]
    return None


def ranked_suggestions(query, candidates):
    """Typo similarity helps review; it never alone confirms a medication."""
    q_strength, q_form = strengths(query), form(query)

    def rank(candidate):
        name = candidate["official_name"]
        compatible = (not q_strength or strengths(name) == q_strength) and (
            not q_form or form(name) == q_form
        )
        return compatible, fuzz.ratio(
            normalize(product_name(query)), normalize(product_name(name))
        )

    return sorted(candidates, key=rank, reverse=True)


def candidate_details(query, candidate):
    name = candidate["official_name"]
    qs, cs = strengths(query), strengths(name)
    qf, cf = form(query), form(name)
    strength_match = (qs == cs) if qs and cs else None
    form_match = (qf == cf) if qf and cf else None
    return {
        "item_seq": candidate["item_seq"],
        "item_name": name,
        "similarity_score": round(
            fuzz.ratio(normalize(product_name(query)), normalize(product_name(name))), 1
        ),
        "strength_match": strength_match,
        "form_match": form_match,
        "permit_status": candidate.get("permit_status"),
        "match_reason": "STRENGTH_CONFLICT"
        if strength_match is False
        else (
            "FORM_CONFLICT"
            if form_match is False
            else (
                "EXACT_NAME"
                if normalize(product_name(query)) == normalize(product_name(name))
                else "SIMILAR_NAME_REQUIRES_CONFIRMATION"
            )
        ),
    }


def search_with_similarity(client, query, search):
    """Bounded prefix fallback obtains official candidates for OCR typos."""
    found = search(client, query, max_pages=1, max_candidates=20)
    if found["errors"] or found["candidates"]:
        return found
    stem = re.split(r"\d|[（(]", product_name(query), maxsplit=1)[0]
    stem = re.sub(r"[^가-힣a-zA-Z]", "", stem)
    # Up to two prefix searches; short broad names are not searched.
    terms = list(dict.fromkeys([stem[:4], stem[:3]]))
    combined = {}
    truncated = found.get("truncated", False)
    for term in terms:
        if len(term) < 3:
            continue
        result = search(client, term, max_pages=1, max_candidates=20)
        if result["errors"]:
            return result
        truncated |= result.get("truncated", False)
        for candidate in result["candidates"]:
            score = candidate_details(query, candidate)["similarity_score"]
            if score >= 70:
                combined[candidate["item_seq"]] = candidate
    return {
        **found,
        "candidates": ranked_suggestions(query, list(combined.values()))[:10],
        "truncated": truncated or len(combined) > 10,
    }


def resolve_exact_scope(client, query, found):
    """Broad fuzzy search truncation must not mask complete full-name search."""
    if not found.get("truncated") or found.get("errors"):
        return found
    items = []
    complete = False
    from app.medicare_final.mfds_api import MFDSError

    try:
        for page in client.pages(
            client.permit_list, name=query, page_size=100, max_pages=2
        ):
            items.extend(page.items)
            complete = page.page * page.page_size >= page.total_count
    except MFDSError:
        return found
    if not complete:
        return found
    candidates = [
        {
            "item_seq": str(item["ITEM_SEQ"]),
            "official_name": item["ITEM_NAME"],
            "permit_status": item.get("CANCEL_NAME"),
        }
        for item in items
        if item.get("ITEM_SEQ") and item.get("ITEM_NAME")
    ]
    exact = [
        c
        for c in candidates
        if normalize(product_name(c["official_name"])) == normalize(product_name(query))
    ]
    if len(exact) == 1:
        return {"errors": [], "truncated": False, "candidates": exact}
    return found


# Category cues printed on pharmacy bags ("{항생제}", "세균에 의한 ...") grouped
# by what the drug is for. Class-level, not product-level, so it does not grow
# with the number of products.
EFFECT_GROUPS = {
    "antibacterial": ("항생제", "항균", "세균", "감염"),
    "anti_inflammatory_pain": ("소염", "진통", "염증", "통증"),
    "fever": ("해열",),
    "antispasmodic": ("진경", "경련", "연축", "산통"),
    "gastric_acid": ("위산", "제산", "궤양"),
}
SIMILAR_MIN = 75
SIMILAR_MARGIN = 15


def effect_groups(text):
    return {g for g, words in EFFECT_GROUPS.items() if any(w in text for w in words)}


def corroborated_candidate(client, query, found, bag_text):
    """Second-chance identity for names the strict rule rejects: an OCR typo or an
    inactive (withdrawn/expired) product.

    Name side: one exact official name, or one clearly closest name (similarity
    >= SIMILAR_MIN, ahead of the next by SIMILAR_MARGIN) with no strength/form
    conflict. Evidence side: the candidate's official effects must share a
    category with cues printed on the bag. Returns (candidate, basis) or None.
    """
    from app.medicare_final.mfds_api import MFDSError

    if found.get("errors") or not found.get("candidates"):
        return None
    cands = found["candidates"]
    key = normalize(product_name(query))
    exact = [c for c in cands if normalize(product_name(c["official_name"])) == key]
    if len(exact) > 1:
        return None
    if exact:
        pick, basis = exact[0], "EXACT_NAME"
    else:
        scored = sorted(
            ((candidate_details(query, c), c) for c in cands),
            key=lambda row: -row[0]["similarity_score"],
        )
        top, pick = scored[0]
        second = scored[1][0]["similarity_score"] if len(scored) > 1 else 0
        if (
            top["similarity_score"] < SIMILAR_MIN
            or top["similarity_score"] - second < SIMILAR_MARGIN
            or top["strength_match"] is False
            or top["form_match"] is False
        ):
            return None
        basis = "SIMILAR_NAME"
    # ponytail: cues come from the whole bag, not the lines next to the name (OCR
    # line order is unreliable), so another drug's cue can corroborate. The name
    # gate above is the primary guard; pair cues by OCR box position if this
    # misfires.
    cues = effect_groups(bag_text)
    if not cues:
        return None
    try:
        page = client.permit_detail(pick["item_seq"], page_size=1)
    except MFDSError:
        return None
    effects = " ".join(str(row.get("EE_DOC_DATA") or "") for row in page.items)
    if not cues & effect_groups(effects):
        return None
    return pick, basis


def identity_note(raw, candidate, basis):
    notes = []
    if basis == "SIMILAR_NAME":
        notes.append(
            f"사진의 '{raw}'와 이름이 가장 비슷하고 약 봉투의 효능 표시와 일치해 "
            "자동으로 찾은 약입니다."
        )
    status = candidate.get("permit_status")
    if status != "정상":
        notes.append(
            f"현재 허가 상태가 '{status or '확인 불가'}'인 제품입니다. "
            "복용 전 약사에게 확인하세요."
        )
    return " ".join(notes) or None
