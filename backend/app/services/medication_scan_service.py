from concurrent.futures import ThreadPoolExecutor

from starlette.concurrency import run_in_threadpool

from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.medications import MedicationScanResult
from app.services.medication_result import partition_result


def _identify(client, item, text, search_provider):
    """One OCR candidate -> MFDS match (or the reason it did not match)."""
    from app.medicare_final.drug_search import search_drug
    from app.services.drug_identity import (
        candidate_details,
        corroborated_candidate,
        identity_note,
        resolve_exact_scope,
        search_with_similarity,
        select_candidate,
    )

    name, raw = item["search_name"], item["raw_text"]
    # Only the extracted drug-shaped name leaves the server. A name on a line
    # with identity words is returned for review, not sent.
    if item["identity_words_on_line"]:
        return {
            "recognized_name": raw,
            "match_status": "UNMATCHED",
            "review_reason": "PRIVACY_REVIEW_REQUIRED",
            "candidates": [],
        }
    if search_provider:
        found = search_provider.search(client, name)
    else:
        found = resolve_exact_scope(
            client, name, search_with_similarity(client, name, search_drug)
        )
    candidate = select_candidate(name, found)
    reason, note = None, None
    if not candidate and (second := corroborated_candidate(client, name, found, text)):
        candidate, basis = second
        reason = "BAG_CORROBORATED_" + basis
        note = identity_note(raw, candidate, basis)
    if not candidate:
        reason = "SEARCH_FAILED" if found.get("errors") else "IDENTITY_UNCERTAIN"
        if any(c.get("permit_status") != "정상" for c in found["candidates"]):
            reason = "INACTIVE_OR_UNCERTAIN_PRODUCT"
    return {
        "recognized_name": raw,
        "match_status": "MATCHED"
        if candidate
        else ("AMBIGUOUS" if found["candidates"] else "UNMATCHED"),
        "item_seq": candidate["item_seq"] if candidate else None,
        "item_name": candidate["official_name"] if candidate else None,
        "review_reason": reason,
        "identity_note": note,
        "candidates": [candidate_details(name, c) for c in found["candidates"]],
    }


def _assemble(text, matches, selected, result):
    """Matches + official MFDS info -> API result."""
    warnings = list(result.get("warnings", []))
    if len(selected) != len(matches):
        warnings.append(
            "일부 약품이 미식별 상태입니다. 전체 처방의 안전성은 확인되지 않았습니다."
        )
    info = result.get("official_drug_info", [])
    return MedicationScanResult(
        recognized_text=text,
        status="COMPLETED"
        if result.get("status") == "ready" and len(selected) == len(matches)
        else ("PARTIAL" if info else "INCOMPLETE"),
        medications=matches,
        **partition_result(matches, result),
        official_drug_info=info,
        official_precautions=result.get("official_precautions", []),
        sources=result.get("sources", {}),
        dur_status="NOT_VERIFIED",
        dur_details=result.get("dur_result", {}),
        information_status="AVAILABLE" if info else "NOT_AVAILABLE",
        processing_status=result["status"].upper(),
        warnings=warnings,
    )


class MedicationScanService:
    def __init__(self, settings, search_provider=None):
        self.settings = settings
        self.search_provider = search_provider

    async def analyze(self, text):
        from app.services.precaution_rewriter import rewrite_guidance

        result = await run_in_threadpool(self._analyze, text)
        return await run_in_threadpool(rewrite_guidance, result, self.settings)

    def _analyze(self, text):
        from app.medicare_final.medication_pipeline import get_medication_info
        from app.medicare_final.mfds_api import MFDSClient, MFDSError
        from app.services.ocr_candidate_adapter import extract_candidates

        key = self.settings.MFDS_API_KEY.get_secret_value()
        if not key:
            raise BusinessException(
                Code.MEDICATION_INFO_UNAVAILABLE, "MFDS_API_KEY 설정이 필요합니다."
            )
        client = MFDSClient(key=key, max_calls=100, retries=0, timeout=10)
        try:
            items = extract_candidates(text)["candidates"]
            # Lookups are independent; 4 workers stay well inside MFDS rate limits.
            with ThreadPoolExecutor(max_workers=4) as pool:
                matches = list(
                    pool.map(
                        lambda i: _identify(client, i, text, self.search_provider),
                        items,
                    )
                )
            selected = [
                m["item_seq"] for m in matches if m["match_status"] == "MATCHED"
            ]
            if not selected:
                return MedicationScanResult(
                    recognized_text=text,
                    status="NEEDS_CONFIRMATION",
                    **partition_result(matches, {}),
                    medications=matches,
                    warnings=["약품을 식별하지 못했습니다. 후보 확인이 필요합니다."],
                )
            # No summary pass: purpose text comes from rewrite_guidance.
            result = get_medication_info(list(dict.fromkeys(selected)), client=client)
            return _assemble(text, matches, selected, result)
        except MFDSError:
            raise BusinessException(Code.MEDICATION_INFO_UNAVAILABLE) from None
        except ValueError:
            raise BusinessException(Code.AI_RESPONSE_INVALID) from None
        finally:
            client.session.close()
