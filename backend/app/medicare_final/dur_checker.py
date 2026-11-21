"""DUR 각 유형과 두 품목 사이의 명시적 병용금기를 보수적으로 확인한다."""

from .mfds_api import MFDSError, DUR_OPS
from .data_processor import SOURCES, clean_document

KINDS = (
    "interaction",
    "age",
    "pregnancy",
    "elderly",
    "dosage",
    "duration",
    "duplicate",
    "split",
)
SAFE_FIELDS = (
    "ITEM_SEQ",
    "ITEM_NAME",
    "INGR_CODE",
    "INGR_KOR_NAME",
    "INGR_ENG_NAME",
    "MIXTURE_ITEM_SEQ",
    "MIXTURE_ITEM_NAME",
    "MIXTURE_INGR_CODE",
    "MIXTURE_INGR_KOR_NAME",
    "MIXTURE_INGR_ENG_NAME",
    "TYPE_NAME",
    "PROHBT_CONTENT",
    "REMARK",
    "NOTIFICATION_DATE",
    "CHANGE_DATE",
    "AGE",
    "AGE_UNIT",
    "FORBIDDEN_CONTENT",
)


def check_type(client, item_seq, kind, *, page_size=100, max_pages=2):
    if kind not in KINDS or kind not in DUR_OPS:
        return {
            "kind": kind,
            "status": "not_supported",
            "records": [],
            "complete": False,
            "source": SOURCES["dur"],
        }
    records, total, complete = [], None, False
    try:
        for page in client.pages(
            client.dur,
            item_seq=str(item_seq),
            kind=kind,
            page_size=page_size,
            max_pages=max_pages,
        ):
            total = page.total_count
            records.extend(page.items)
            complete = page.page * page.page_size >= total and len(records) >= total
            if not page.items and len(records) < total:
                complete = False
                break
    except MFDSError as exc:
        return {
            "kind": kind,
            "status": "api_error",
            "error": exc.category,
            "code": exc.code,
            "records": [],
            "complete": False,
            "source": SOURCES["dur"],
        }
    # 조건 필드명을 추측해 누락하지 않도록 불필요한 사업자·이미지 필드만 제외한다.
    clean_records = [
        {
            k.strip(): clean_document(v) if isinstance(v, str) else v
            for k, v in row.items()
            if k.strip() not in ("BIZRNO", "BIG_PRDT_IMG_URL", "ITEM_IMAGE")
            and v not in (None, "")
        }
        for row in records
    ]
    # 동일 행을 제거하더라도 완결성은 API 원본 행 수로 판단한다.
    unique = []
    for row in clean_records:
        if row not in unique:
            unique.append(row)
    positive = (
        "contraindication_found"
        if kind in ("interaction", "age", "pregnancy")
        else "registered_rule_found"
    )
    status = (
        "incomplete"
        if not complete
        else (positive if records else "no_registered_rule_found")
    )
    return {
        "kind": kind,
        "status": status,
        "records": unique,
        "complete": complete,
        "total_count": total,
        "received_count": len(records),
        "source": SOURCES["dur"],
    }


def check_pair(first_seq, second_seq, first, second):
    """DUR 원행이 두 품목코드와 두 DUR 성분코드를 함께 제시할 때만 양성.

    성분명이나 허가 API의 M 코드와 DUR D 코드를 이름으로 이어 붙이지 않는다.
    """
    if first_seq == second_seq:
        return {
            "status": "unknown",
            "reason": "서로 다른 두 품목이 필요합니다.",
            "records": [],
        }
    errors = any(
        x.get("status") in ("api_error", "not_supported") for x in (first, second)
    )
    matches = []
    for expected, partner, result in (
        (str(first_seq), str(second_seq), first),
        (str(second_seq), str(first_seq), second),
    ):
        for row in result.get("records", []):
            if (
                str(row.get("ITEM_SEQ")) == expected
                and str(row.get("MIXTURE_ITEM_SEQ")) == partner
                and row.get("INGR_CODE")
                and row.get("MIXTURE_INGR_CODE")
            ):
                matches.append(row)
    if matches:
        return {
            "status": "contraindication_found",
            "records": matches,
            "complete": bool(first.get("complete") and second.get("complete")),
            "source": SOURCES["dur"],
            "basis": "동일 DUR 행의 두 품목코드 및 두 DUR 성분코드",
        }
    if errors:
        return {"status": "api_error", "records": [], "complete": False}
    if not first.get("complete") or not second.get("complete"):
        return {"status": "incomplete", "records": [], "complete": False}
    # 조회가 비어 있으면 DUR 성분 식별자의 품목 매핑을 입증할 수 없다.
    return {
        "status": "unknown",
        "records": [],
        "complete": True,
        "reason": "두 품목에 대한 검증된 DUR 성분 매핑이 충분하지 않습니다. 안전성은 확인되지 않았습니다.",
    }
