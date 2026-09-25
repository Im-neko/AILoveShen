"""loguru によるログの設定。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ailoveshen.infrastructure.config import LoggingSettings


def setup_logging(
    settings: "LoggingSettings | None" = None,
    *,
    level: str = "INFO",
    log_file: str | Path | None = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
    compression: str = "zip",
    format_string: str | None = None,
    debug: bool = False,
) -> None:
    """
    loguru でログを設定する。

    コンソールとファイルの両方に、構造化した出力、ローテーション、保持期間を
    設定する。

    Args:
        settings: 設定の LoggingSettings。渡したときは他の引数を無視する。
        level: ログレベル（DEBUG、INFO、WARNING、ERROR、CRITICAL）。
        log_file: ログファイルのパス。None ならファイルに出さない。
        rotation: ローテーションの契機（例: "10 MB"、"1 day"、"00:00"）。
        retention: ローテーションしたファイルを残す期間（例: "7 days"、"10 files"）。
        compression: ローテーションしたファイルの圧縮形式（例: "zip"、"gz"）。
        format_string: ログメッセージの独自のフォーマット文字列。
        debug: デバッグモードにする（トレースバックに変数の値を出す）。
               注意: 秘密情報が漏れないよう、本番では False にすること。
    """
    # 設定が渡されたらそこから取り出す
    if settings is not None:
        level = settings.level
        log_file = settings.file
        rotation = settings.rotation
        retention = settings.retention
        compression = settings.compression
        format_string = settings.format
        # 明示されていなければ、ログレベルから debug を決める
        debug = debug or level.upper() == "DEBUG"

    # 既定のフォーマット
    if format_string is None:
        format_string = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )

    # 既定のハンドラーを外す
    logger.remove()

    # 色付きのコンソールのハンドラーを加える
    # diagnose=True はトレースバックに変数の値を出す。本番ではセキュリティ上の危険がある
    logger.add(
        sys.stderr,
        format=format_string,
        level=level,
        colorize=True,
        backtrace=True,
        diagnose=debug,  # デバッグモードのときだけ有効にする
    )

    # log_file が指定されていればファイルのハンドラーを加える
    if log_file is not None:
        log_path = Path(log_file)

        # 必要なら親ディレクトリを作る
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 色コードを除いたファイル用のフォーマット
        file_format = format_string.replace("<green>", "").replace("</green>", "")
        file_format = file_format.replace("<level>", "").replace("</level>", "")
        file_format = file_format.replace("<cyan>", "").replace("</cyan>", "")

        logger.add(
            str(log_path),
            format=file_format,
            level=level,
            rotation=rotation,
            retention=retention,
            compression=compression,
            encoding="utf-8",
            backtrace=True,
            diagnose=debug,  # デバッグモードのときだけ有効にする
        )

    logger.info(f"ログを初期化した（レベル {level}）")


def get_logger(name: str | None = None) -> Any:
    """
    ロガーを返す。

    Args:
        name: ロガーの名前（通常は __name__）。None ならルートのロガーを返す。

    Returns:
        ロガー（loguru.Logger）。
    """
    if name is None:
        return logger
    return logger.bind(name=name)


# 使いやすいように logger を再エクスポートする
__all__ = ["logger", "setup_logging", "get_logger"]
