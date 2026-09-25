"""Let Gemini define the town the mission asks for (docs/design/09 M3/M4, the next design doc).

The mission says "make it a town" but nothing says what a town is or when it is done. Gemini is
given the mission, what the streamer can do now and the goal vocabulary, and asked for a
definition whose parts can be judged from the world, with the abilities each part still needs.
Run several times to see how much the definition varies.

    GEMINI_API_KEY=... python spikes/town_definition.py [--runs 3]
"""

import argparse
import asyncio
import json

from ailoveshen.infrastructure.adapters.gemini.gemini_text_generator import GeminiTextGenerator
from ailoveshen.infrastructure.adapters.prompts.prompt_template_builder import ABILITIES
from ailoveshen.infrastructure.adapters.prompts.stream_context import format_conditions
from ailoveshen.infrastructure.config import load_settings

PROMPT = """\
あなたは Minecraft のサバイバルで暮らす AI 配信者「シェン」の方針を決めます。
配信の大目標は「{mission}」です。この「街」とは何か、いつ完成したと言えるかを定義してください。

## 今の状況
- 家（5x5 の木の家、ベッドとチェストあり）が 1 軒ある。サバイバルで、夜は敵が湧く
- 視聴者はコメントで話しかけてくる。配信で完成まで見せられる規模にする

## 今できること
{abilities}

## 今ゲームの状態から判定できる条件
{conditions}

## 定義の決まり
- 街の完成条件は、ゲームの状態から自動で判定できるものだけにする（「にぎやか」「きれい」のような
  判定できない言葉は、数や配置の条件に言い換える）
- 条件ごとに、今の判定できる条件で表せるか、表せないなら何を判定・実行できる必要があるかを書く
- 段階（先に作るもの → 後で作るもの）に分ける。前の段階が後の段階の準備になるように
- 作るのが大きすぎて配信で終わらないものは入れない

## 出力
指定の JSON で出力してください。
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "definition": {"type": "string", "description": "この配信での「街」の定義（2〜3文）"},
        "stages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string", "description": "この段階が街に要る理由"},
                    "criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "condition": {
                                    "type": "string",
                                    "description": "完成条件（数・配置で判定できる形）",
                                },
                                "judged_now": {
                                    "type": "boolean",
                                    "description": "今の判定できる条件で表せるか",
                                },
                                "as_predicate": {
                                    "type": "string",
                                    "description": "表せるなら今の条件での書き方",
                                },
                                "needs": {
                                    "type": "string",
                                    "description": "表せない・実行できないなら、要る判定や行動",
                                },
                            },
                            "required": ["condition", "judged_now", "needs"],
                        },
                    },
                },
                "required": ["title", "why", "criteria"],
            },
        },
    },
    "required": ["definition", "stages"],
}


async def main(runs: int) -> None:
    s = load_settings()
    generator = GeminiTextGenerator(
        api_key=s.gemini.api_key,
        model=s.gemini.main_model,
        thinking_level="medium",
        max_output_tokens=s.gemini.max_output_tokens,
    )
    prompt = PROMPT.format(
        mission=s.minecraft.mission.text,
        abilities=ABILITIES,
        conditions=format_conditions(),
    )
    for i in range(runs):
        data = await generator.generate_json(prompt, SCHEMA)
        print(f"=== run {i + 1} ===")
        print(json.dumps(data, ensure_ascii=False, indent=1))
    await generator.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    asyncio.run(main(parser.parse_args().runs))
