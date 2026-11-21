def partition_result(matches, result):
    identified = {}
    uncertain = []
    info = {str(row["item_seq"]): row for row in result.get("official_drug_info", [])}
    for match in matches:
        if match["match_status"] != "MATCHED" or not match.get("item_seq"):
            uncertain.append(match)
            continue
        seq = match["item_seq"]
        if seq not in identified:
            summary = [
                row
                for row in result.get("ai_summary") or []
                if row.get("item_seq") == seq
                and row.get("status")
                in {"verified_extractive", "supported_with_checks"}
                and row.get("text")
            ]
            summary_status = "AI_VALIDATED" if summary else "WITHHELD"
            if not summary:
                effects = (info.get(seq) or {}).get("effects", [])
                if isinstance(effects, list):
                    summary = [
                        {
                            "text": row["text"],
                            "source_ids": [row["source_id"]]
                            if row.get("source_id")
                            else [],
                            "status": "official_excerpt",
                        }
                        for row in effects
                        if isinstance(row, dict) and row.get("text")
                    ]
                if summary:
                    summary_status = "OFFICIAL_EXCERPT"
            identified[seq] = {
                "item_seq": seq,
                "item_name": match["item_name"],
                "recognized_names": [],
                "summary": summary,
                "summary_status": summary_status,
                "official_info": info.get(seq),
                "precautions": [
                    row
                    for row in result.get("official_precautions", [])
                    if str(row.get("item_seq")) == seq
                ],
            }
        identified[seq]["recognized_names"].append(match["recognized_name"])
        identified[seq].setdefault("identity_note", match.get("identity_note"))
        if not identified[seq]["identity_note"]:
            identified[seq]["identity_note"] = match.get("identity_note")
    values = list(identified.values())
    return {
        "identified_count": len(values),
        "uncertain_count": len(uncertain),
        "summarized_count": sum(bool(row["summary"]) for row in values),
        "identified_medications": values,
        "uncertain_medications": uncertain,
    }
