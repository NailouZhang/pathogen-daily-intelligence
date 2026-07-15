from src.pdi2.translation import _looks_chinese, _repair_zh


def test_repair_forbidden_hantavirus_terms():
    text = _repair_zh("WHO宣布宋病毒疫情结束", [])
    assert "汉坦病毒" in text
    assert "宋病毒" not in text


def test_translation_validator_preserves_numbers():
    assert _looks_chinese("共报告13例，其中3例死亡", "13 cases and 3 deaths")[0]
    ok, reason = _looks_chinese("共报告十三例", "13 cases")
    assert not ok and reason == "number_loss"
