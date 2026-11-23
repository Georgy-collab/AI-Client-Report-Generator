"""Генерация PDF-отчётов из HTML через Jinja2 + WeasyPrint."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateError, TemplateNotFound, select_autoescape
from weasyprint import HTML

from utils.exceptions import PDFGenerationError
from utils.logging_config import get_logger

logger = get_logger("pdf_generator")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
REPORTS_DIR = PROJECT_ROOT / "reports"

REPORT_PRESETS: dict[str, dict[str, Any]] = {
    "client": {
        "template": "report_template.html",
        "prefix": "report",
        "required_fields": (
            "client_name",
            "topic",
            "main_request",
            "mood",
            "next_steps",
        ),
    },
    "logo": {
        "template": "logo_report_template.html",
        "prefix": "logo_report",
        "required_fields": (
            "client_name",
            "company_name",
            "industry",
            "brand_values",
            "style_preferences",
            "colors",
            "mood",
            "logo_concept",
            "logo_prompt",
            "next_steps",
        ),
    },
}


def _build_filename(prefix: str = "report", created_at: datetime | None = None) -> str:
    moment = created_at or datetime.now()
    return f"{prefix}_{moment.strftime('%Y-%m-%d_%H-%M')}.pdf"


def _validate_report_data(
    data: dict[str, Any],
    required_fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(data, dict) or not data:
        raise PDFGenerationError("Нет данных для формирования отчёта")

    normalized = dict(data)
    missing = [field for field in required_fields if field not in normalized]
    if missing:
        logger.warning(
            "В данных отчёта отсутствуют поля, подставляем заглушки: %s",
            ", ".join(missing),
        )
        for field in missing:
            normalized[field] = "Не указано"
    return normalized


def render_html(data: dict[str, Any], template_name: str = "report_template.html") -> str:
    """Подставляет данные отчёта в HTML-шаблон."""
    if not TEMPLATES_DIR.exists():
        raise PDFGenerationError(
            "Папка шаблонов не найдена",
            details=str(TEMPLATES_DIR),
        )

    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise PDFGenerationError(
            f"HTML-шаблон не найден: {template_name}",
            details=str(template_path),
        )

    try:
        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        template = env.get_template(template_name)
        context = {
            **data,
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        }
        html = template.render(**context)
    except TemplateNotFound as exc:
        raise PDFGenerationError(
            f"HTML-шаблон не найден: {template_name}",
            details=str(exc),
        ) from exc
    except TemplateError as exc:
        logger.exception("Ошибка шаблона Jinja2")
        raise PDFGenerationError(
            "Ошибка при рендере HTML-шаблона",
            details=str(exc),
        ) from exc

    if not html.strip():
        raise PDFGenerationError("Шаблон вернул пустой HTML")

    logger.debug("HTML отрендерен (%d символов)", len(html))
    return html


def generate_pdf(
    data: dict[str, Any],
    output_dir: str | Path | None = None,
    template_name: str | None = None,
    *,
    report_type: str = "client",
    filename_prefix: str | None = None,
) -> Path:
    """
    Рендерит HTML-шаблон и сохраняет PDF в папку reports/.
    report_type: "client" | "logo"
    """
    preset = REPORT_PRESETS.get(report_type)
    if preset is None:
        raise PDFGenerationError(
            f"Неизвестный тип отчёта: {report_type}",
            details="Допустимо: client, logo",
        )

    chosen_template = template_name or preset["template"]
    prefix = filename_prefix or preset["prefix"]
    report_data = _validate_report_data(data, preset["required_fields"])
    reports_dir = Path(output_dir) if output_dir else REPORTS_DIR

    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PDFGenerationError(
            "Не удалось создать папку для отчётов",
            details=str(exc),
        ) from exc

    logger.info(
        "Генерация PDF (type=%s, template=%s, dir=%s)",
        report_type,
        chosen_template,
        reports_dir,
    )

    try:
        html_content = render_html(report_data, template_name=chosen_template)
        output_path = reports_dir / _build_filename(prefix)

        if output_path.exists():
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            output_path = reports_dir / f"{prefix}_{stamp}.pdf"
            logger.debug("Файл уже существует, используем имя: %s", output_path.name)

        HTML(string=html_content, base_url=str(TEMPLATES_DIR)).write_pdf(str(output_path))
    except PDFGenerationError:
        raise
    except OSError as exc:
        logger.exception("Ошибка записи PDF на диск")
        raise PDFGenerationError(
            "Не удалось сохранить PDF-файл",
            details=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка WeasyPrint при генерации PDF")
        raise PDFGenerationError(
            "Не удалось сконвертировать HTML в PDF",
            details=str(exc),
        ) from exc

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise PDFGenerationError(
            "PDF не был создан или файл пуст",
            details=str(output_path),
        )

    logger.info("PDF сохранён: %s (%d байт)", output_path, output_path.stat().st_size)
    return output_path
