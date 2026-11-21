import re

from app.medicare_final.drug_search import normalize

# Whole-line tokens only; exclude prose suffixes such as 금액/시키고.
DRUG_LINE = re.compile(
    r"^[*•\s]*([가-힣A-Za-z][가-힣A-Za-z0-9]{1,35}?(?:당의정|서방정|캡슐|캅셀|정|시럽|연고|크림)(?:\s*\d+(?:\.\d+)?[가-힣A-Za-zμ%·.]*)?)\s*$"
)


def extract_candidates(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 30000:
        raise ValueError("Invalid OCR input")
    items, seen = [], set()
    offset = 0
    for line in text.splitlines(keepends=True):
        match = DRUG_LINE.fullmatch(line.strip())
        if match:
            raw = match.group(1)
            if (
                not any(
                    word in raw
                    for word in ("병원", "약국", "환자", "성명", "처방", "등록")
                )
                and normalize(raw) not in seen
            ):
                seen.add(normalize(raw))
                start = offset + line.index(raw)
                items.append(
                    {
                        "raw_text": raw,
                        "search_name": raw,
                        "evidence": {"start": start, "end": start + len(raw)},
                    }
                )
        offset += len(line)
    return {"candidates": items[:12], "truncated": len(items) > 12}
