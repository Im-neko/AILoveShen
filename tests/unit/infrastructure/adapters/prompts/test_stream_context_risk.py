from ailoveshen.infrastructure.adapters.prompts.stream_context import _format_risk


class TestFormatRisk:
    def test_shows_level_factors_and_advice(self):
        lines = _format_risk(
            {
                "level": "high",
                "factors": ["night", "hostiles: zombie 3m"],
                "advice": ["eat first"],
            }
        )
        assert lines == ["- 外に出る危なさ: 高い（night; hostiles: zombie 3m）。出る前に: eat first"]

    def test_omits_advice_when_none(self):
        (line,) = _format_risk({"level": "low", "factors": ["bare hands, no armor"], "advice": []})
        assert line == "- 外に出る危なさ: 低い（bare hands, no armor）"

    def test_nothing_without_a_known_level(self):
        assert _format_risk({}) == []
        assert _format_risk({"level": "weird"}) == []
