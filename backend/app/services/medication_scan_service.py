from concurrent.futures import ThreadPoolExecutor

from starlette.concurrency import run_in_threadpool

from app.core.exceptions import BusinessException
from app.core.response_codes import Code
from app.schemas.medications import MedicationScanResult
from app.services.medication_result import partition_result


class MedicationScanService:
    def __init__(self, settings, search_provider=None):
        self.settings = settings
        self.search_provider = search_provider

    async def analyze(self, text):
        from app.services.precaution_rewriter import rewrite_guidance

        result = await run_in_threadpool(self._analyze, text)
        return await run_in_threadpool(rewrite_guidance, result, self.settings)

    def _analyze(self, text):
        from app.medicare_final.clova_client import ClovaClient, ClovaError
        from app.medicare_final.drug_search import search_drug
        from app.medicare_final.medication_pipeline import (
            get_medication_info,
        )
        from app.medicare_final.mfds_api import MFDSClient, MFDSError
        from app.services.drug_identity import (
            candidate_details,
            corroborated_candidate,
            identity_note,
            resolve_exact_scope,
            search_with_similarity,
            select_candidate,
        )
        from app.services.ocr_candidate_adapter import extract_candidates

        settings = self.settings
        if not settings.MFDS_API_KEY.get_secret_value():
            raise BusinessException(
                Code.MEDICATION_INFO_UNAVAILABLE, "MFDS_API_KEY 설정이 필요합니다."
            )
        client = MFDSClient(
            key=settings.MFDS_API_KEY.get_secret_value(),
            max_calls=100,
            retries=0,
            timeout=10,
        )
        model = None
        try:
            extraction = extract_candidates(text)

            def lookup(item):
                name = item["search_name"]
                # Only the extracted drug-shaped name leaves the server. A name on
                # a line with identity words is returned for review, not sent.
                raw = item["raw_text"]
                if item["identity_words_on_line"]:
                    return {
                        "recognized_name": raw,
                        "match_status": "UNMATCHED",
                        "review_reason": "PRIVACY_REVIEW_REQUIRED",
                        "candidates": [],
                    }
                found = (
                    self.search_provider.search(client, name)
                    if self.search_provider
                    else search_with_similarity(client, name, search_drug)
                )
                if self.search_provider is None:
                    found = resolve_exact_scope(client, name, found)
                candidate = select_candidate(name, found)
                reason, note = None, None
                if not candidate and (
                    second := corroborated_candidate(client, name, found, text)
                ):
                    candidate, basis = second
                    reason = "BAG_CORROBORATED_" + basis
                    note = identity_note(raw, candidate, basis)
                if not candidate:
                    reason = (
                        "SEARCH_FAILED" if found.get("errors") else "IDENTITY_UNCERTAIN"
                    )
                if not candidate and any(
                    c.get("permit_status") != "정상" for c in found["candidates"]
                ):
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
                    "candidates": [
                        candidate_details(name, c) for c in found["candidates"]
                    ],
                }

            # Lookups are independent; 4 workers stay well inside MFDS rate limits.
            with ThreadPoolExecutor(max_workers=4) as pool:
                matches = list(pool.map(lookup, extraction["candidates"]))
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
            if settings.CLOVA_API_KEY.get_secret_value():
                model = ClovaClient(
                    api_key=settings.CLOVA_API_KEY.get_secret_value(),
                    model=settings.CLOVA_MODEL,
                    retries=0,
                    timeout=settings.AI_REQUEST_TIMEOUT_SECONDS,
                )
            result = get_medication_info(
                list(dict.fromkeys(selected)),
                client=client,
                model_client=model,
                allow_generation=model is not None,
            )
            validation = result.get("validation_result", {}).get("status", "not_run")
            summaries = [
                row
                for row in result.get("ai_summary") or []
                if row.get("status") in {"verified_extractive", "supported_with_checks"}
                and row.get("text")
            ]
            result["ai_summary"] = summaries
            visible = bool(summaries)
            warnings = list(result.get("warnings", []))
            if len(selected) != len(matches):
                warnings.append(
                    "일부 약품이 미식별 상태입니다. 전체 처방의 안전성은 확인되지 않았습니다."
                )
            if model is None:
                warnings.append("CLOVA_API_KEY 미설정으로 AI 생성을 생략했습니다.")
            return MedicationScanResult(
                recognized_text=text,
                status="COMPLETED"
                if visible
                and result.get("status") == "ready"
                and len(selected) == len(matches)
                else ("PARTIAL" if result.get("official_drug_info") else "INCOMPLETE"),
                medications=matches,
                **partition_result(matches, result),
                official_drug_info=result.get("official_drug_info", []),
                official_precautions=result.get("official_precautions", []),
                sources=result.get("sources", {}),
                dur_status="NOT_VERIFIED",
                dur_details=result.get("dur_result", {}),
                information_status="AVAILABLE"
                if result.get("official_drug_info")
                else "NOT_AVAILABLE",
                validation_status=validation,
                processing_status=result["status"].upper(),
                ai_summary={"medications": summaries} if summaries else None,
                warnings=warnings,
            )
        except (MFDSError, ClovaError):
            raise BusinessException(Code.MEDICATION_INFO_UNAVAILABLE) from None
        except ValueError:
            raise BusinessException(Code.AI_RESPONSE_INVALID) from None
        finally:
            client.session.close()
            if model:
                model.session.close()
