"""
AI Client Report Generator
Формирует PDF-отчёты по транскрибациям диалогов с клиентами.
Тип отчёта (client / logo) по умолчанию определяет ИИ.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from utils.ai_processor import build_report_data
from utils.exceptions import (
    AIProcessingError,
    AppError,
    ConfigError,
    PDFGenerationError,
    TranscriptionError,
)
from utils.logging_config import get_logger, setup_logging
from utils.pdf_generator import generate_pdf

logger = get_logger("main")


def read_transcription(source: str | None) -> str:
    """Читает транскрибацию из файла или из stdin / интерактивного ввода."""
    if source:
        path = Path(source)
        logger.debug("Чтение транскрибации из файла: %s", path)

        if not path.exists():
            raise TranscriptionError(f"Файл не найден: {path}")
        if not path.is_file():
            raise TranscriptionError(f"Указанный путь не является файлом: {path}")

        try:
            text = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError as exc:
            raise TranscriptionError(
                f"Не удалось прочитать файл как UTF-8: {path}",
                details=str(exc),
            ) from exc
        except OSError as exc:
            raise TranscriptionError(
                f"Ошибка чтения файла: {path}",
                details=str(exc),
            ) from exc

        if not text:
            raise TranscriptionError(f"Файл пуст: {path}")

        logger.info("Транскрибация загружена из файла (%d символов)", len(text))
        return text

    print("Вставьте транскрибацию диалога.")
    print("Завершите ввод пустой строкой (или Ctrl+Z затем Enter в Windows):\n")

    lines: list[str] = []
    try:
        while True:
            line = input()
            if line == "" and lines:
                break
            lines.append(line)
    except EOFError:
        logger.debug("Получен EOF при ручном вводе")
    except KeyboardInterrupt:
        raise

    text = "\n".join(lines).strip()
    if not text:
        raise TranscriptionError("Транскрибация не получена")

    logger.info("Транскрибация получена из ввода (%d символов)", len(text))
    return text


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def run(source: str | None = None, report_type: str = "auto") -> Path:
    logger.info("Старт генерации отчёта (type=%s)", report_type)

    print("1/3 Чтение транскрибации...")
    transcription = read_transcription(source)

    if report_type == "auto":
        print("2/3 ИИ определяет тип отчёта и готовит данные...")
    elif report_type == "logo":
        print("2/3 Анализ лого-диалога и генерация примера логотипа...")
    else:
        print("2/3 Анализ диалога через ProxyAPI...")

    chosen_type, report_data, detection = build_report_data(
        transcription,
        report_type=report_type,
    )

    reason = detection.get("reason", "")
    confidence = detection.get("confidence", "")
    print(f"   → выбран тип: {chosen_type}" + (f" ({confidence})" if confidence else ""))
    if reason:
        print(f"   → {reason}")

    print("3/3 Генерация PDF...")
    pdf_path = generate_pdf(report_data, report_type=chosen_type)

    relative = _relative_path(pdf_path)
    message = f"Отчёт успешно создан: {relative}"
    logger.info(message)
    print(message)

    logo_path = report_data.get("logo_image_path")
    if logo_path:
        print(f"Пример логотипа сохранён: {_relative_path(Path(logo_path))}")

    return pdf_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI Client Report Generator — PDF-отчёты по диалогам с клиентами",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Путь к текстовому файлу с транскрибацией (если не указан — ввод вручную)",
    )
    parser.add_argument(
        "--type",
        dest="report_type",
        choices=["auto", "client", "logo"],
        default="auto",
        help="Тип отчёта: auto (ИИ сам выбирает, по умолчанию), client или logo",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Запустить простой Flask API на http://127.0.0.1:5000",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Уровень логирования (по умолчанию INFO)",
    )
    return parser


def create_app():
    """Опциональный Flask API для генерации отчётов по HTTP."""
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/generate")
    def generate():
        payload = request.get_json(silent=True)
        if payload is None:
            logger.warning("API /generate: тело запроса не JSON")
            return jsonify({"ok": False, "error": "Ожидается JSON-тело запроса"}), 400

        text = (payload.get("text") or "").strip()
        if not text:
            logger.warning("API /generate: пустое поле text")
            return jsonify({"ok": False, "error": "Поле 'text' обязательно"}), 400

        report_type = (payload.get("type") or "auto").strip().lower()
        if report_type not in {"auto", "client", "logo"}:
            return jsonify(
                {
                    "ok": False,
                    "error": "Поле 'type' должно быть 'auto', 'client' или 'logo'",
                }
            ), 400

        logger.info("API /generate: старт (type=%s, chars=%d)", report_type, len(text))
        try:
            chosen_type, report_data, detection = build_report_data(
                text,
                report_type=report_type,
            )
            pdf_path = generate_pdf(report_data, report_type=chosen_type)
            logger.info("API /generate: успех type=%s → %s", chosen_type, pdf_path)

            public_report = {
                key: value
                for key, value in report_data.items()
                if key not in {"logo_image_b64", "logo_image_data_uri"}
            }

            return jsonify(
                {
                    "ok": True,
                    "type": chosen_type,
                    "detection": detection,
                    "report": public_report,
                    "pdf": str(pdf_path),
                    "message": f"Отчёт успешно создан: {pdf_path.as_posix()}",
                }
            )
        except (TranscriptionError, ConfigError, AIProcessingError, PDFGenerationError) as exc:
            status = 400 if isinstance(exc, (TranscriptionError, ConfigError)) else 502
            logger.error("API /generate: %s — %s", type(exc).__name__, exc)
            return jsonify({"ok": False, "error": str(exc)}), status
        except Exception as exc:  # noqa: BLE001
            logger.exception("API /generate: необработанная ошибка")
            return jsonify({"ok": False, "error": f"Внутренняя ошибка: {exc}"}), 500

    return app


def _exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, KeyboardInterrupt):
        return 130
    if isinstance(exc, ConfigError):
        return 2
    if isinstance(exc, TranscriptionError):
        return 3
    if isinstance(exc, AIProcessingError):
        return 4
    if isinstance(exc, PDFGenerationError):
        return 5
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(level=args.log_level)

    if args.serve:
        logger.info("Запуск Flask API на http://127.0.0.1:5000")
        app = create_app()
        print("Flask API: http://127.0.0.1:5000")
        print('POST /generate  JSON: {"text": "...", "type": "auto|client|logo"}')
        try:
            app.run(host="127.0.0.1", port=5000, debug=False)
            return 0
        except OSError as exc:
            logger.error("Не удалось запустить Flask: %s", exc)
            print(f"Ошибка запуска API: {exc}", file=sys.stderr)
            return 1

    try:
        run(args.file, report_type=args.report_type)
        return 0
    except KeyboardInterrupt:
        logger.warning("Прервано пользователем")
        print("\nОтменено пользователем.")
        return 130
    except AppError as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        print(f"Ошибка: {exc}", file=sys.stderr)
        return _exit_code_for(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Необработанная ошибка")
        print(f"Неожиданная ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
