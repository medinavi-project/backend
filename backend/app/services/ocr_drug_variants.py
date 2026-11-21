import re

from app.medicare_final.drug_search import normalize


def collect_names(text, extracted, limit=10):
    """Keep literal OCR spellings; never silently merge similar medications."""
    names = list(extracted)
    pattern = re.compile(
        r"^[*•\s]*([가-힣A-Za-z][가-힣A-Za-z0-9 ]{1,35}?(?:당의정|캡슐|서방정|정|시럽|연고)(?:\s*\d+(?:\.\d+)?[가-힣A-Za-zμ%]*)?)\s*$"
    )
    for line in text.splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            names.append(match.group(1))
    result, seen = [], set()
    for name in names:
        name = name.strip().lstrip("*•").strip()
        key = normalize(name)
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result[:limit]
