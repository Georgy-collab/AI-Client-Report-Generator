"""Настройка логирования: консоль + файл logs/app.log."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
DEFAULT_LOG_FILE = LOGS_DIR / "app.log"

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """
    Инициализирует корневой логгер приложения один раз.
    Пишет в консоль и в файл logs/app.log.
    """
    global _CONFIGURED

    logger = logging.getLogger("ai_client_report")
    if _CONFIGURED:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        return logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    target = log_file or DEFAULT_LOG_FILE

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric_level)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(console)
    logger.addHandler(file_handler)

    # Чуть тише сторонние библиотеки в консоли
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.ERROR)
    logging.getLogger("fontTools").setLevel(logging.ERROR)

    _CONFIGURED = True
    logger.debug("Логирование инициализировано: level=%s, file=%s", level, target)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Возвращает дочерний логгер приложения."""
    base = "ai_client_report"
    if name:
        return logging.getLogger(f"{base}.{name}")
    return logging.getLogger(base)
