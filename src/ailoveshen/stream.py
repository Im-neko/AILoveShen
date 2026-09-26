"""
本番の配信を始める（docs/streaming.md）。

    python -m ailoveshen.stream [--control candidates|tools] [--no-speak] [--no-board]
        [--board-port 8765] [--no-chat] [--debug]

リポジトリの直下で動かす（config/ と .env を読む）。前もって Minecraft サーバー、TTS サーバー、
ブリッジ（cd minecraft-bridge && npm start）を起動しておく。止めるのは Ctrl-C。

既定は設定（config/default.yaml の stream と twitch）: 目標ボードとアバター（:8765）、読み上げ、
Twitch のチャット（.env の TWITCH_CHANNEL があれば）。プレイは失敗しても止まらず、待って
やり直す。ステップの上限はない。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from ailoveshen.factories.stream import StreamSetupError, create_stream
from ailoveshen.infrastructure.config import load_config_dict, load_settings
from ailoveshen.infrastructure.logging import setup_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ailoveshen.stream", description="AILoveShen の配信を始める"
    )
    parser.add_argument(
        "--control",
        choices=["candidates", "tools"],
        help="行動の決め方（既定は設定の minecraft.agent.control）",
    )
    parser.add_argument("--no-speak", action="store_true", help="読み上げない（TTS を使わない）")
    parser.add_argument("--no-board", action="store_true", help="目標ボードとアバターを出さない")
    parser.add_argument(
        "--board-port", type=int, help="目標ボードのポート（既定は stream.board_port）"
    )
    parser.add_argument("--no-chat", action="store_true", help="Twitch のチャットを読まない")
    parser.add_argument("--debug", action="store_true", help="DEBUG のログも出す")
    parser.add_argument(
        "--config-dir", type=Path, default=Path("config"), help="設定のディレクトリ（既定 config）"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_dir: Path = args.config_dir
    if not (config_dir / "default.yaml").is_file():
        print(
            f"{config_dir / 'default.yaml'} がない。リポジトリの直下で動かす（cd AILoveShen）",
            file=sys.stderr,
        )
        return 2
    settings = load_settings(config_dir=config_dir)
    if args.control:
        settings.minecraft.control = args.control
    if args.no_speak:
        settings.stream.speak = False
    if args.board_port is not None:
        settings.stream.board_port = args.board_port
    if args.no_board:
        settings.stream.board_port = 0
    if args.no_chat:
        settings.twitch.enabled = False
    if args.debug:
        # 思考の要約は毎回の出力のトークンを使う: デバッグのときだけ（/debug/gemini に出る）
        settings.gemini.include_thoughts = True
    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", settings.gemini.api_key),
            ("TYPESAFE_API_KEY", settings.jev.api_key),
        )
        if not value
    ]
    if missing:
        print(f".env に {', '.join(missing)} を書く（cp .env.example .env）", file=sys.stderr)
        return 2
    setup_logging(
        level="DEBUG" if args.debug else "INFO",
        log_file=settings.logging.file or None,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        compression=settings.logging.compression,
    )
    tts_config = load_config_dict(config_dir).get("tts", {})
    try:
        asyncio.run(_run(settings, tts_config, config_dir.resolve().parent))
    except KeyboardInterrupt:
        print("\n[stop] Ctrl-C で止めた", flush=True)
        return 130
    except StreamSetupError as e:
        logger.error(str(e))
        return 1
    return 0


async def _run(settings, tts_config, base_dir: Path) -> None:
    stream = await create_stream(settings, tts_config, base_dir)
    await stream.run()


if __name__ == "__main__":
    sys.exit(main())
