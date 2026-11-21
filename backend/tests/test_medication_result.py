from app.services.medication_result import partition_result


def test_partition_counts_and_only_verified_summaries():
    matches = [
        {
            "recognized_name": "약A",
            "match_status": "MATCHED",
            "item_seq": "1",
            "item_name": "공식A",
        },
        {
            "recognized_name": "약B",
            "match_status": "MATCHED",
            "item_seq": "2",
            "item_name": "공식B",
        },
        {"recognized_name": "모호", "match_status": "AMBIGUOUS", "candidates": []},
    ]
    result = {
        "ai_summary": [
            {"item_seq": "1", "text": "설명", "status": "verified_extractive"},
            {"item_seq": "2", "text": "미검증", "status": "review_required"},
        ]
    }
    data = partition_result(matches, result)
    assert data["identified_count"] == 2 and data["uncertain_count"] == 1
    assert data["summarized_count"] == 1
    assert data["identified_medications"][1]["summary"] == []
