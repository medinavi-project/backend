"""식약처 품목 응답을 근거별로 정제한다. 원본과 정제 텍스트는 분리 보관한다."""

import html
import re
import hashlib
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup, NavigableString, Tag

SOURCES = {
    "easy": "https://www.data.go.kr/data/15075057/openapi.do",
    "permit": "https://www.data.go.kr/data/15095677/openapi.do",
    "permit_list": "https://www.data.go.kr/data/15095677/openapi.do",
    "ingredients": "https://www.data.go.kr/data/15095677/openapi.do",
    "dur": "https://www.data.go.kr/data/15059486/openapi.do",
}
FIELDS = {
    "effects": [("easy", "efcyQesitm"), ("permit", "EE_DOC_DATA")],
    "dosage": [("easy", "useMethodQesitm"), ("permit", "UD_DOC_DATA")],
    "warnings": [("easy", "atpnWarnQesitm")],
    "precautions": [("easy", "atpnQesitm"), ("permit", "NB_DOC_DATA")],
    "side_effects": [("easy", "seQesitm")],
    "interactions": [("easy", "intrcQesitm")],
    "storage": [("easy", "depositMethodQesitm"), ("permit", "STORAGE_METHOD")],
}


def clean_document(value):
    if value is None or not str(value).strip():
        return ""
    raw = html.unescape(str(value))
    if not re.search(r"<[A-Za-z!?/][^>]*>", raw):
        return re.sub(r"\n{3,}", "\n\n", raw.replace("\r\n", "\n")).strip()
    soup = BeautifulSoup(raw, "html.parser")
    for x in soup(["script", "style"]):
        x.decompose()
    # 식약처 XML의 임상적으로 중요한 조항 제목이 속성에 들어 있다.
    for node in soup.find_all(["doc", "section", "article"]):
        title = node.get("title")
        if title and str(title).strip():
            node.insert(0, NavigableString("\n" + str(title).strip() + "\n"))
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for cell in tr.find_all(["th", "td"], recursive=False):
                prefix = "".join(
                    f"[{attr}={cell[attr]}]"
                    for attr in ("rowspan", "colspan")
                    if cell.get(attr)
                )
                cells.append(prefix + " ".join(cell.stripped_strings))
            if cells:
                rows.append(" | ".join(cells))
        table.replace_with(NavigableString("\n" + "\n".join(rows) + "\n"))
    for node in soup.find_all(
        [
            "br",
            "p",
            "div",
            "li",
            "section",
            "title",
            "paragraph",
            "article",
            "h1",
            "h2",
            "h3",
            "tr",
        ]
    ):
        if node.name == "br":
            node.replace_with(NavigableString("\n"))
        else:
            node.insert_before(NavigableString("\n"))
            node.insert_after(NavigableString("\n"))
    text = soup.get_text()
    return "\n".join(
        line.strip() for line in re.sub(r"\n{3,}", "\n\n", text).splitlines()
    ).strip()


def now_kst():
    return datetime.now(timezone(timedelta(hours=9))).isoformat()


def process_drug(item_seq, responses, *, retrieved_at=None):
    """responses: 출처별 {status, items, total_count?}; 오류는 빈 결과와 구별."""
    seq = str(item_seq)
    retrieved_at = retrieved_at or now_kst()
    selected = {}
    for origin in ("easy", "permit", "permit_list", "ingredients"):
        part = responses.get(origin, {"status": "not_requested", "items": []})
        items = part.get("items") or []
        code_field = "itemSeq" if origin == "easy" else "ITEM_SEQ"
        if any(str(row.get(code_field, "")).strip() != seq for row in items):
            raise ValueError("출처 품목기준코드 불일치")
        selected[origin] = items
    easy = (selected["easy"] or [{}])[0]
    permit = (selected["permit"] or [{}])[0]
    permit_list = (selected["permit_list"] or [{}])[0]
    result = {
        "drug_name": permit.get("ITEM_NAME") or easy.get("itemName"),
        "item_seq": seq,
        "manufacturer": permit.get("ENTP_NAME") or easy.get("entpName"),
        "drug_type": permit_list.get("SPCLTY_PBLC"),
        "ingredients": [],
        "strength": [],
        "dosage_form": None,
        "appearance": permit.get("CHART"),
        "approval_status": permit.get("CANCEL_NAME"),
        "updated_at": {
            "easy": easy.get("updateDe"),
            "permit": permit.get("CHANGE_DATE"),
        },
        "retrieved_at": retrieved_at,
        "source": {},
        "field_status": {},
        "conflicts": {},
        "documents": [],
        "raw_documents": [],
    }
    for origin in ("easy", "permit", "permit_list", "ingredients"):
        part = responses.get(origin, {"status": "not_requested"})
        result["source"][origin] = {
            "url": SOURCES[origin],
            "status": part.get("status"),
            "total_count": part.get("total_count"),
            "retrieved_at": retrieved_at,
            "error": part.get("error"),
        }
    for row in selected["ingredients"]:
        ingredient = {
            k: row.get(field)
            for k, field in [
                ("name", "MTRAL_NM"),
                ("code", "MTRAL_CODE"),
                ("quantity", "QNT"),
                ("unit_code", "INGD_UNIT_CD"),
                ("content", "CPNT_CTNT_CONT"),
            ]
        }
        if ingredient not in result["ingredients"]:
            result["ingredients"].append(ingredient)
        if (
            row.get("QNT")
            and {
                "quantity": row.get("QNT"),
                "unit_code": row.get("INGD_UNIT_CD"),
                "ingredient": row.get("MTRAL_NM"),
            }
            not in result["strength"]
        ):
            result["strength"].append(
                {
                    "quantity": row.get("QNT"),
                    "unit_code": row.get("INGD_UNIT_CD"),
                    "ingredient": row.get("MTRAL_NM"),
                }
            )
    for field, mappings in FIELDS.items():
        docs = []
        for origin, key in mappings:
            row = easy if origin == "easy" else permit
            raw = row.get(key)
            clean = clean_document(raw)
            if clean:
                identifier = hashlib.sha256(
                    (seq + "\0" + origin + "\0" + key + "\0" + clean).encode("utf-8")
                ).hexdigest()[:20]
                doc = {
                    "source_id": "MFDS-" + identifier,
                    "field": field,
                    "source": origin,
                    "source_field": key,
                    "url": SOURCES[origin],
                    "text": clean,
                }
                result["raw_documents"].append(
                    {
                        "field": field,
                        "source": origin,
                        "source_field": key,
                        "text": str(raw),
                    }
                )
                if not any(d["text"] == clean for d in docs):
                    docs.append(doc)
        result[field] = docs
        result["documents"].extend(docs)
        if len(docs) > 1 and len({x["text"] for x in docs}) > 1:
            # 다르게 서술되었다는 사실만 표시하며 의료적 모순 여부는 검토 대상으로 남긴다.
            result["conflicts"][field] = "source_difference_requires_review"
        relevant_statuses = [
            result["source"][origin]["status"] for origin, _ in mappings
        ]
        result["field_status"][field] = (
            "found"
            if docs
            else ("api_error" if "api_error" in relevant_statuses else "not_found")
        )
    return result
