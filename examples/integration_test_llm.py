#!/usr/bin/env python3
"""本物の Gemini API で LLM パイプラインを確かめる結合テスト。

前提:
1. 環境変数 GEMINI_API_KEY を設定してある
2. 依存をインストールしてある: pip install "ailoveshen[llm]"
3. （--speak のときだけ）Style-Bert-VITS2 のサーバーが動いていて、
   "ailoveshen[tts]" をインストールしてある

使い方:
    python examples/integration_test_llm.py
    python examples/integration_test_llm.py --speak   # TTS で読み上げもする
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# プロジェクトの src をパスに足す
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ailoveshen.domain.value_objects import EmotionState, EmotionType
from ailoveshen.domain.entities import Conversation
from ailoveshen.factories.llm import create_llm_service
from ailoveshen.infrastructure.config import load_settings, load_yaml_file
from ailoveshen.infrastructure.events import AsyncEventBus

CONFIG_DIR = Path(__file__).parent.parent / "config"


async def run(speak: bool) -> bool:
    """実況とチャットへの返事を生成し、指定があれば読み上げる。"""
    settings = load_settings(config_dir=CONFIG_DIR)
    event_bus = AsyncEventBus()

    print("=" * 60)
    print(
        f"モデル: {settings.gemini.main_model} "
        f"(thinking_level={settings.gemini.main_thinking_level})"
    )
    print("=" * 60)

    llm = create_llm_service(
        gemini=settings.gemini,
        character=settings.character,
        event_publisher=event_bus,
        conversation=Conversation(),
    )

    tts = None
    if speak:
        from ailoveshen.factories.tts import create_and_connect_tts_service

        # create_tts_service は設定の tts セクション（voice/synthesis/queue/audio）を
        # そのまま受け取る
        tts_config = load_yaml_file(CONFIG_DIR / "default.yaml").get("tts", {})
        tts = await create_and_connect_tts_service(
            config=tts_config,
            event_publisher=event_bus,
            get_current_emotion=llm.get_current_emotion,
        )

    ok = True
    try:
        steps = [
            (
                "実況",
                lambda: llm.generate_commentary(
                    recent_events=["洞窟の入り口を見つけた"],
                ),
            ),
            (
                "チャットへの返事",
                lambda: llm.generate_response(
                    "neko",
                    "がんばれー！何を探してるの？",
                ),
            ),
            (
                "実況（履歴あり）",
                lambda: llm.generate_commentary(
                    recent_events=["ゾンビに遭遇", "ゾンビを倒した"],
                ),
            ),
        ]
        for i, (label, step) in enumerate(steps):
            if i == 2:
                llm.update_emotion(EmotionState(EmotionType.EXCITED, 0.8))
            started = time.monotonic()
            text = await step()
            elapsed = time.monotonic() - started
            print(f"\n[{label}] {elapsed:.2f}s")
            print(f"  {text or '（空）'}")
            ok = ok and bool(text)
            if tts and text:
                await tts.speak(text, source=label)

        if tts:
            print("\n読み上げが終わるのを待っている...")
            await tts.wait_until_idle()
    finally:
        await llm.close()
        if tts:
            await tts.stop()

    print("\n" + ("すべての生成がテキストを返した" if ok else "空の生成があった"))
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--speak", action="store_true", help="結果を TTS で読み上げる")
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(run(args.speak)) else 1)


if __name__ == "__main__":
    main()
