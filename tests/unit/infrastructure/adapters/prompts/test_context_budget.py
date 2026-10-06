"""ツール選択のプロンプトの固定部分の大きさの上限（docs/design/90）。

1 ステップの時間は入力のトークン数でほぼ決まる（設計書 51 §2: 呼び出し時間の約 97%）。道具の定義と
ルールの文は毎ステップ送るので、増えたら全ステップが遅くなる。この上限を超えたら、先に削れるものを
削る。それでも要るときだけ上限を上げ、理由を設計書 90 に書く（上限が低すぎるのは上限のほうの誤り）。

数え方は `estimate_tokens`（日本語は 1 字 1 トークンで、実測より約 2 割多い。2026-10-06: 38 個の定義が
推定 8,399 に対し、サーバーの報告は 6,943）。ステップごとに変わる部分（今していること、まわり、直近の道具）は
ここでは測れない。実際の記録から `tools/prompt_budget.py` で見る。
"""

import json
import re

from ailoveshen.application.use_cases.tool_catalog import tool_specs
from ailoveshen.infrastructure.adapters.local_llm.schema_check import estimate_tokens
from ailoveshen.infrastructure.adapters.prompts import game_prompt_template_builder as prompts

# 2026-10-06 の値に約 5% の余裕（推定のトークン）
TOOL_DEFINITIONS_BUDGET = {
    "production": (dict(skills=True), 8800),  # 配信のふだん（技あり、画面なし）
    "stuck": (dict(skills=True, experiment=True), 9450),  # 行き詰まり（inspect / diagnostics が加わる）
}
RULES_BUDGET = 1450  # TOOL_TEMPLATE の固定の文（差し込む欄を除く）
STUCK_SECTION_BUDGET = 780
SKILLS_SECTION_BUDGET = 190


def _definitions_tokens(**kwargs) -> int:
    specs = tool_specs(**kwargs)
    return estimate_tokens(
        json.dumps(
            [
                {
                    "type": "function",
                    "function": {"name": s.name, "description": s.description, "parameters": s.parameters},
                }
                for s in specs
            ],
            ensure_ascii=False,
        )
    )


def _static(template: str) -> int:
    return estimate_tokens(re.sub(r"\$\w+", "", template))


def test_tool_definitions_stay_within_budget():
    for label, (kwargs, budget) in TOOL_DEFINITIONS_BUDGET.items():
        used = _definitions_tokens(**kwargs)
        assert used <= budget, (
            f"道具の定義（{label}）が {used} トークン（上限 {budget}）。毎ステップ送るので、"
            "削るか、上げる理由を設計書 90 に書く"
        )


def test_rules_and_sections_stay_within_budget():
    assert _static(prompts.TOOL_TEMPLATE.template) <= RULES_BUDGET
    assert _static(prompts.TOOL_STUCK_SECTION) <= STUCK_SECTION_BUDGET
    assert _static(prompts.TOOL_SKILLS_SECTION) <= SKILLS_SECTION_BUDGET


def test_the_budget_is_tight_enough_to_notice_growth():
    """上限が緩すぎて何も捕まえなくならないように: 余裕は 1 割以内。"""
    for kwargs, budget in TOOL_DEFINITIONS_BUDGET.values():
        assert budget <= _definitions_tokens(**kwargs) * 1.10
    assert RULES_BUDGET <= _static(prompts.TOOL_TEMPLATE.template) * 1.10
