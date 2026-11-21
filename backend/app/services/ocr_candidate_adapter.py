import re

from app.medicare_final.drug_search import normalize
from app.medicare_final.medication_pipeline import (
    extract_candidates as dictionary_candidates,
)

IDENTITY_WORDS = ("병원", "약국", "환자", "성명", "처방", "등록")
FORMS = r"(?:당의정|서방정|캡슐|캅셀|정|시럽|연고|크림)"
UNIT = r"(?:mg|mcg|μg|g|ml|%|[밀일]리?그[램람럼]|마이크로그[램람]|밀리리터)"
# Name at line start, after optional list markers. Text after the name (dosing
# columns such as "1 3 3" or "1정 3회") is ignored, never part of the query.
DRUG_START = re.compile(
    r"^[ \t]*(?:[*•·]|\(?\d{1,2}[.)]|\[\d{1,2}\]|[①-⑳])?[ \t]*"
    r"([가-힣A-Za-z][가-힣A-Za-z0-9]{1,35}? ?" + FORMS + r"(?:"
    # Attached strength keeps OCR unit typos (e.g. 250일리그럼) for review.
    r"\d+(?:[./]\d+)*[가-힣A-Za-zμ%·.]*"
    # Spaced strength needs a unit, so a dose count like "1정" is not absorbed.
    r"|[ \t]\d+(?:[./]\d+)*[ \t]?" + UNIT + r")?)(?![가-힣A-Za-z0-9])",
    re.IGNORECASE,
)
TRAILING_COUNT = re.compile(r"(?:[ \t]+\d+)+$")
# A dictionary hit alone is weak: with ~43k names, most 3-letter words are one
# edit from some product (마세요 -> 아세요정). It must also look like a drug name.
DRUG_SHAPED = re.compile(FORMS + r"|\d+(?:[./]\d+)*[ \t]?" + UNIT, re.IGNORECASE)
STRONG_HIT = 90
# Drug categories, not product names (해열제 시럽, 소화제).
GENERIC = re.compile(
    r"^(?:해열|진통|해열진통|소화|항생|소염|진해거담|거담|지사|변비|수면|안정|영양)제"
    r"|^(?:감기|위장|변비|소화|안|물)약"
)


def _dictionary_hit_ok(raw, hints):
    if DRUG_SHAPED.search(raw):
        return True
    top = max((c["similarity"] for c in hints["candidates"]), default=0)
    return len(normalize(raw)) >= 4 and top >= STRONG_HIT


def _line_of(text, start):
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    return text[line_start : line_end if line_end != -1 else len(text)]


def extract_candidates(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 30000:
        raise ValueError("Invalid OCR input")
    items = []
    offset = 0
    for line in text.splitlines(keepends=True):
        match = DRUG_START.match(line)
        if match:
            start = offset + match.start(1)
            items.append((start, start + len(match.group(1))))
        offset += len(line)
    # Upstream dictionary windows catch names the line pattern misses. Only
    # dictionary hits are used; its plain regex hits (e.g. 약제비총액) are noise.
    for row in dictionary_candidates(text)["candidates"]:
        hints = row.get("local_dictionary", {})
        if hints.get("status") != "candidates_found":
            continue
        start, end = row["evidence"]["start"], row["evidence"]["end"]
        if not _dictionary_hit_ok(text[start:end], hints):
            continue
        trimmed = TRAILING_COUNT.sub("", text[start:end])
        trimmed = re.sub("(" + FORMS + ")[은는이가을를도]$", r"\1", trimmed)
        end = start + len(trimmed)
        if not any(s < end and start < e for s, e in items):
            items.append((start, end))
    result, seen = [], set()
    for start, end in sorted(items):
        raw = text[start:end]
        key = normalize(raw)
        if (
            not key
            or key in seen
            or GENERIC.match(key)
            or any(w in raw for w in IDENTITY_WORDS)
        ):
            continue
        seen.add(key)
        line = _line_of(text, start)
        result.append(
            {
                "raw_text": raw,
                "search_name": re.sub(r"\s+", "", raw),
                "evidence": {"start": start, "end": end},
                "identity_words_on_line": any(w in line for w in IDENTITY_WORDS),
            }
        )
    return {"candidates": result[:12], "truncated": len(result) > 12}
