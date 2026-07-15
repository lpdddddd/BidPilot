from __future__ import annotations

import logging
import sys
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

_CONSOLE = Console(stderr=True)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=_CONSOLE, rich_tracebacks=True, show_path=False)],
        force=True,
    )
    return logging.getLogger("bidpilot_data")


def get_logger(name: str = "bidpilot_data") -> logging.Logger:
    return logging.getLogger(name)


def log_stats(logger: logging.Logger, title: str, stats: dict[str, Any]) -> None:
    logger.info("%s | %s", title, stats)
