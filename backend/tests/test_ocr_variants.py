from app.services.ocr_drug_variants import collect_names


def test_multiple_literal_spellings_are_preserved():
    text = "세프로바이정250밀리그램\n씨프로바이정250일리그럼\n*라니드정\n라니드정\n부스코판당의정\n총수납금액\n2,300원"
    names = collect_names(text, ["세프로바이정250밀리그램", "*라니드정"])
    assert "씨프로바이정250일리그럼" in names
    assert names.count("라니드정") == 1
    assert "부스코판당의정" in names
    assert "총수납금액" not in names
