"""Separate product identity from generated-answer citation validation."""

import re

from rapidfuzz import fuzz

from app.medicare.drug_search import form, normalize, strengths


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
