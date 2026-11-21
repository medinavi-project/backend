from app.schemas.medications import IdentifiedMedicationDTO, MedicationScanResult
from app.services.medication_presentation import present_scan


def test_five_sections_and_no_extra_public_fields():
    result = MedicationScanResult(
        recognized_text="text",
        status="PARTIAL",
        identified_medications=[
            IdentifiedMedicationDTO(
                item_seq="1",
                item_name="테스트약",
                recognized_names=["테스트약"],
                summary_status="OFFICIAL_EXCERPT",
                official_info={
                    "effects": [{"text": "효능"}],
                    "dosage": [{"text": "공식 용법"}],
                    "storage": [{"text": "보관"}],
                },
                precautions=[{"official_text": "주의"}],
            )
        ],
    )
    data = present_scan(result).model_dump()
    assert set(data) == {
        "status",
        "identified_count",
        "uncertain_count",
        "medications",
        "uncertain_medications",
    }
    card = data["medications"][0]
    assert set(card) == {
        "item_seq",
        "name",
        "description",
        "guidance_status",
        "warning",
    }
    assert card["warning"] is None
    assert set(card["description"]) == {
        "purpose",
        "how_to_take",
        "precautions",
        "side_effects",
        "storage",
    }
    assert card["description"]["side_effects"] is None
    assert card["description"]["how_to_take"] == "공식 용법"
    assert card["guidance_status"] == "PARTIAL"
