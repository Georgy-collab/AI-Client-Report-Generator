"""Взаимодействие с ИИ через ProxyAPI (чат + генерация изображений)."""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from utils.exceptions import AIProcessingError, ConfigError
from utils.logging_config import get_logger

load_dotenv()

logger = get_logger("ai_processor")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGO_ASSETS_DIR = PROJECT_ROOT / "reports" / "logos"

CLIENT_FIELDS = (
    "client_name",
    "topic",
    "main_request",
    "mood",
    "next_steps",
)

LOGO_FIELDS = (
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
)

CLIENT_SYSTEM_PROMPT = """Ты — аналитик клиентских диалогов.
По транскрибации разговора верни ТОЛЬКО валидный JSON без markdown и пояснений.
Структура ответа строго такая:
{
  "client_name": "имя клиента или 'Не указано'",
  "topic": "краткая тема разговора",
  "main_request": "основной запрос клиента",
  "mood": "настроение клиента (например: спокойный, раздражённый, заинтересованный)",
  "next_steps": "рекомендуемые следующие шаги для менеджера"
}
Все значения — строки на русском языке.
"""

LOGO_SYSTEM_PROMPT = """Ты — бренд-стратег и арт-директор.
По транскрибации обсуждения логотипа компании верни ТОЛЬКО валидный JSON без markdown.
Структура ответа строго такая:
{
  "client_name": "имя клиента или 'Не указано'",
  "company_name": "название компании",
  "industry": "сфера деятельности",
  "brand_values": "ценности и ассоциации бренда",
  "style_preferences": "желаемый визуальный стиль (минимализм, геометрия и т.д.)",
  "colors": "предпочтительная палитра",
  "mood": "настроение клиента в разговоре",
  "logo_concept": "краткое описание концепции логотипа на русском",
  "logo_prompt": "детальный ENGLISH prompt для генерации логотипа: clean vector logo, flat design, centered, white background, no mockups, no photorealism",
  "next_steps": "следующие шаги для менеджера/дизайнера"
}
Все поля кроме logo_prompt — на русском. logo_prompt — только на английском, 1-3 предложения.
"""

DETECT_SYSTEM_PROMPT = """Ты классификатор клиентских диалогов.
Определи, о чём в первую очередь разговор, и верни ТОЛЬКО JSON:
{
  "report_type": "logo" или "client",
  "confidence": "high" | "medium" | "low",
  "reason": "кратко на русском, почему выбран этот тип"
}

Правила:
- "logo" — если обсуждают логотип, фирменный знак, айдентику, бренд-символ, дизайн знака, цвета/стиль именно для логотипа.
- "client" — любые другие темы: поддержка, продажи, жалобы, внедрение, договоры, общие вопросы и т.п.
- Если логотип упоминается мимоходом, а основная тема другая — выбирай "client".
- Если есть сомнение, но просят концепцию/вариант знака — выбирай "logo".
"""


def _get_client(*, timeout: float | None = None) -> OpenAI:
    api_key = os.getenv("PROXYAPI_KEY", "").strip()
    if not api_key or api_key == "your_proxyapi_key_here":
        raise ConfigError(
            "Не задан PROXYAPI_KEY. Укажите ключ ProxyAPI в файле .env"
        )

    base_url = os.getenv("PROXYAPI_BASE_URL", "https://api.proxyapi.ru/v1").strip()
    logger.debug("Инициализация клиента ProxyAPI: base_url=%s", base_url)
    kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def _wrap_api_error(exc: Exception, fallback: str) -> AIProcessingError:
    if isinstance(exc, AuthenticationError):
        logger.error("Ошибка авторизации ProxyAPI: %s", exc)
        return AIProcessingError(
            "Неверный ключ ProxyAPI. Проверьте PROXYAPI_KEY в .env",
            details=str(exc),
        )
    if isinstance(exc, RateLimitError):
        logger.error("Превышен лимит запросов ProxyAPI: %s", exc)
        return AIProcessingError(
            "Превышен лимит запросов ProxyAPI. Попробуйте позже",
            details=str(exc),
        )
    if isinstance(exc, APITimeoutError):
        logger.error("Таймаут запроса к ProxyAPI: %s", exc)
        return AIProcessingError(
            "Таймаут при обращении к ProxyAPI",
            details=str(exc),
        )
    if isinstance(exc, APIConnectionError):
        logger.error("Ошибка соединения с ProxyAPI: %s", exc)
        return AIProcessingError(
            "Не удалось подключиться к ProxyAPI. Проверьте интернет",
            details=str(exc),
        )
    if isinstance(exc, APIStatusError):
        logger.error("ProxyAPI вернул HTTP %s: %s", exc.status_code, exc)
        return AIProcessingError(
            f"ProxyAPI вернул ошибку HTTP {exc.status_code}",
            details=str(exc),
        )
    logger.exception("Неожиданная ошибка ProxyAPI")
    return AIProcessingError(fallback, details=str(exc))


def _extract_json(raw: str, required_fields: tuple[str, ...]) -> dict[str, str]:
    """Достаёт JSON из ответа модели (в т.ч. из markdown-блока)."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise AIProcessingError(
                "Не удалось разобрать JSON из ответа ИИ",
                details=str(exc),
            ) from exc
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as nested_exc:
            raise AIProcessingError(
                "Ответ ИИ содержит невалидный JSON",
                details=str(nested_exc),
            ) from nested_exc

    if not isinstance(data, dict):
        raise AIProcessingError("Ответ ИИ должен быть JSON-объектом")

    result: dict[str, str] = {}
    missing: list[str] = []
    for field in required_fields:
        if field not in data or data[field] in (None, ""):
            missing.append(field)
            result[field] = "Не указано"
        else:
            result[field] = str(data[field]).strip()

    if missing:
        logger.warning("В ответе ИИ отсутствуют поля: %s", ", ".join(missing))

    return result


def _chat_json(
    *,
    system_prompt: str,
    user_prompt: str,
    required_fields: tuple[str, ...],
    model: str | None = None,
) -> dict[str, str]:
    chosen_model = (model or os.getenv("PROXYAPI_MODEL", "openai/gpt-4o-mini")).strip()
    logger.info("Chat Completions через ProxyAPI (model=%s)", chosen_model)

    try:
        client = _get_client(timeout=90.0)
        response = client.chat.completions.create(
            model=chosen_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_api_error(exc, "Не удалось получить ответ от ИИ") from exc

    try:
        content = response.choices[0].message.content
    except (IndexError, AttributeError) as exc:
        logger.error("Некорректная структура ответа ProxyAPI: %s", exc)
        raise AIProcessingError(
            "Некорректный ответ от ProxyAPI",
            details=str(exc),
        ) from exc

    if not content:
        raise AIProcessingError("Модель вернула пустой ответ")

    logger.debug("Сырой ответ модели (%d символов)", len(content))
    return _extract_json(content, required_fields)


def detect_report_type(text: str) -> dict[str, str]:
    """
    Определяет тип отчёта по транскрибации.
    Возвращает: report_type (client|logo), confidence, reason.
    """
    dialog = (text or "").strip()
    if not dialog:
        raise AIProcessingError("Транскрибация пуста — нечего классифицировать")

    # Для классификации достаточно начала диалога — экономим токены
    snippet = dialog if len(dialog) <= 4000 else dialog[:4000]

    logger.info("Классификация типа отчёта (chars=%d)", len(dialog))
    result = _chat_json(
        system_prompt=DETECT_SYSTEM_PROMPT,
        user_prompt=(
            "Определи тип отчёта для этой транскрибации.\n\n"
            f"Транскрибация:\n{snippet}"
        ),
        required_fields=("report_type", "confidence", "reason"),
    )

    report_type = result["report_type"].strip().lower()
    if report_type not in {"client", "logo"}:
        logger.warning(
            "Неожиданный report_type=%r, используем client",
            result["report_type"],
        )
        report_type = "client"
        result["reason"] = (
            f"Классификатор вернул неизвестный тип; выбран client. "
            f"Исходная причина: {result.get('reason', '')}"
        )

    result["report_type"] = report_type
    logger.info(
        "Выбран тип отчёта: %s (confidence=%s). Причина: %s",
        report_type,
        result.get("confidence"),
        result.get("reason"),
    )
    return result


def process_dialog_with_ai(text: str) -> dict[str, str]:
    """Анализ обычного клиентского диалога → данные для PDF-отчёта."""
    dialog = (text or "").strip()
    if not dialog:
        raise AIProcessingError("Транскрибация пуста — нечего анализировать")

    logger.info("Анализ клиентского диалога (chars=%d)", len(dialog))
    result = _chat_json(
        system_prompt=CLIENT_SYSTEM_PROMPT,
        user_prompt=(
            "Проанализируй транскрибацию диалога с клиентом "
            "и верни JSON по заданной схеме.\n\n"
            f"Транскрибация:\n{dialog}"
        ),
        required_fields=CLIENT_FIELDS,
    )
    logger.info(
        "Диалог проанализирован: client=%s, topic=%s",
        result.get("client_name"),
        result.get("topic"),
    )
    return result


def process_logo_dialog_with_ai(text: str) -> dict[str, str]:
    """Анализ обсуждения логотипа → бриф + prompt для генерации картинки."""
    dialog = (text or "").strip()
    if not dialog:
        raise AIProcessingError("Транскрибация пуста — нечего анализировать")

    logger.info("Анализ лого-диалога (chars=%d)", len(dialog))
    result = _chat_json(
        system_prompt=LOGO_SYSTEM_PROMPT,
        user_prompt=(
            "Проанализируй обсуждение логотипа компании с клиентом "
            "и верни JSON по заданной схеме.\n\n"
            f"Транскрибация:\n{dialog}"
        ),
        required_fields=LOGO_FIELDS,
    )
    logger.info(
        "Лого-диалог проанализирован: company=%s, client=%s",
        result.get("company_name"),
        result.get("client_name"),
    )
    return result


def generate_logo_image(
    prompt: str,
    *,
    save_dir: str | Path | None = None,
) -> dict[str, str]:
    """
    Генерирует пример логотипа через ProxyAPI Images.
    Возвращает dict с logo_image_b64, logo_image_mime, logo_image_path.
    """
    clean_prompt = (prompt or "").strip()
    if not clean_prompt or clean_prompt == "Не указано":
        raise AIProcessingError(
            "Пустой logo_prompt — нельзя сгенерировать изображение логотипа"
        )

    model = os.getenv("PROXYAPI_IMAGE_MODEL", "openai/gpt-image-1-mini").strip()
    size = os.getenv("PROXYAPI_IMAGE_SIZE", "1024x1024").strip()
    logger.info(
        "Генерация логотипа через ProxyAPI (model=%s, size=%s, prompt_len=%d)",
        model,
        size,
        len(clean_prompt),
    )

    # Усиливаем промпт под логотип, если модель ещё не получила явные ограничения
    image_prompt = clean_prompt
    if "logo" not in clean_prompt.lower():
        image_prompt = (
            f"Professional company logo design: {clean_prompt}. "
            "Clean vector style, flat design, centered composition, "
            "plain white background, no photorealism, no mockup, no 3D."
        )

    try:
        client = _get_client(timeout=180.0)
        response = client.images.generate(
            model=model,
            prompt=image_prompt,
            size=size,  # type: ignore[arg-type]
            n=1,
        )
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_api_error(
            exc,
            "Не удалось сгенерировать изображение логотипа",
        ) from exc

    try:
        item = response.data[0]
        b64 = getattr(item, "b64_json", None)
    except (IndexError, AttributeError, TypeError) as exc:
        raise AIProcessingError(
            "ProxyAPI вернул пустой результат генерации изображения",
            details=str(exc),
        ) from exc

    if not b64:
        raise AIProcessingError(
            "В ответе ProxyAPI нет изображения (ожидалось data[0].b64_json)"
        )

    # Валидация base64 и сохранение PNG на диск
    try:
        raw_bytes = base64.b64decode(b64, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise AIProcessingError(
            "Некорректный base64 изображения от ProxyAPI",
            details=str(exc),
        ) from exc

    if len(raw_bytes) < 100:
        raise AIProcessingError("Сгенерированное изображение слишком маленькое или пустое")

    out_dir = Path(save_dir) if save_dir else LOGO_ASSETS_DIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = out_dir / f"logo_{stamp}.png"
        out_path.write_bytes(raw_bytes)
    except OSError as exc:
        raise AIProcessingError(
            "Не удалось сохранить файл логотипа",
            details=str(exc),
        ) from exc

    logger.info("Логотип сохранён: %s (%d байт)", out_path, len(raw_bytes))
    return {
        "logo_image_b64": b64,
        "logo_image_mime": "image/png",
        "logo_image_path": str(out_path),
        "logo_image_data_uri": f"data:image/png;base64,{b64}",
    }


def process_logo_report(text: str) -> dict[str, str]:
    """Полный пайплайн лого-отчёта: анализ диалога + генерация примера логотипа."""
    brief = process_logo_dialog_with_ai(text)
    image = generate_logo_image(brief["logo_prompt"])
    return {**brief, **image}


def build_report_data(
    text: str,
    report_type: str = "auto",
) -> tuple[str, dict[str, str], dict[str, str]]:
    """
    Готовит данные отчёта.
    Если report_type == "auto" — ИИ сам выбирает client или logo.

    Возвращает: (итоговый_тип, данные_отчёта, метаданные_классификации).
    """
    chosen = (report_type or "auto").strip().lower()
    detection: dict[str, str] = {
        "report_type": chosen,
        "confidence": "manual",
        "reason": "Тип отчёта задан вручную",
    }

    if chosen == "auto":
        detection = detect_report_type(text)
        chosen = detection["report_type"]
    elif chosen not in {"client", "logo"}:
        raise AIProcessingError(
            f"Неизвестный тип отчёта: {report_type}",
            details="Допустимо: auto, client, logo",
        )

    if chosen == "logo":
        data = process_logo_report(text)
    else:
        data = process_dialog_with_ai(text)

    return chosen, data, detection
