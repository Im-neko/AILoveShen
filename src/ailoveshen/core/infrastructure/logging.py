"""Logging configuration using loguru."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from ailoveshen.core.infrastructure.config import LoggingSettings


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
    Setup logging with loguru.

    Configures both console and file logging with structured output,
    rotation, and retention policies.

    Args:
        settings: LoggingSettings from config. If provided, other args are ignored.
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Path to log file. If None, file logging is disabled.
        rotation: When to rotate (e.g., "10 MB", "1 day", "00:00").
        retention: How long to keep rotated files (e.g., "7 days", "10 files").
        compression: Compression format for rotated files (e.g., "zip", "gz").
        format_string: Custom format string for log messages.
        debug: Enable debug mode (shows variable values in tracebacks).
               WARNING: Set to False in production to avoid leaking secrets.
    """
    # Extract settings if provided
    if settings is not None:
        level = settings.level
        log_file = settings.file
        rotation = settings.rotation
        retention = settings.retention
        compression = settings.compression
        format_string = settings.format
        # Infer debug from log level if not explicitly set
        debug = debug or level.upper() == "DEBUG"

    # Default format
    if format_string is None:
        format_string = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )

    # Remove default handler
    logger.remove()

    # Add console handler with color
    # diagnose=True shows variable values in tracebacks - SECURITY RISK in production
    logger.add(
        sys.stderr,
        format=format_string,
        level=level,
        colorize=True,
        backtrace=True,
        diagnose=debug,  # Only enable in debug mode
    )

    # Add file handler if log_file is specified
    if log_file is not None:
        log_path = Path(log_file)

        # Create parent directories if needed
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # File format without color codes
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
            diagnose=debug,  # Only enable in debug mode
        )

    logger.info(f"Logging initialized at level {level}")


def get_logger(name: str | None = None) -> Any:
    """
    Get a logger instance.

    Args:
        name: Logger name (typically __name__). If None, returns the root logger.

    Returns:
        Logger instance (loguru.Logger).
    """
    if name is None:
        return logger
    return logger.bind(name=name)


# Re-export logger for convenience
__all__ = ["logger", "setup_logging", "get_logger"]
