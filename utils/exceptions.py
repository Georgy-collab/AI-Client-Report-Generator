"""Доменные исключения приложения."""

from __future__ import annotations


class AppError(Exception):
    """Базовая ошибка приложения с понятным сообщением для пользователя."""

    def __init__(self, message: str, *, details: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} ({self.details})"
        return self.message


class ConfigError(AppError):
    """Ошибка конфигурации (.env, ключи, пути)."""


class TranscriptionError(AppError):
    """Ошибка чтения или валидации транскрибации."""


class AIProcessingError(AppError):
    """Ошибка анализа диалога через ProxyAPI / ИИ."""


class PDFGenerationError(AppError):
    """Ошибка рендера HTML или записи PDF."""
