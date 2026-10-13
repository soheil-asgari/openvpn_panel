"""Minimal i18n support using JSON catalogues in ``app/i18n``.

Usage::

    from app.core.i18n import translator
    t = translator("fa")
    t("login.title")

Missing keys fall back to English, then to the key itself.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent.parent / "i18n"
SUPPORTED_LANGUAGES = ("en", "fa")
RTL_LANGUAGES = {"fa", "ar", "he"}


class Translator:
    def __init__(self, language: str, catalogues: dict[str, dict[str, str]]) -> None:
        self.language = language if language in catalogues else "en"
        self._catalogues = catalogues

    def __call__(self, key: str, **kwargs: object) -> str:
        value = (
            self._catalogues.get(self.language, {}).get(key)
            or self._catalogues.get("en", {}).get(key)
            or key
        )
        if kwargs:
            try:
                return value.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return value
        return value

    @property
    def is_rtl(self) -> bool:
        return self.language in RTL_LANGUAGES


@lru_cache
def _load_catalogues() -> dict[str, dict[str, str]]:
    catalogues: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGUAGES:
        path = I18N_DIR / f"{lang}.json"
        if path.exists():
            catalogues[lang] = json.loads(path.read_text(encoding="utf-8"))
        else:
            catalogues[lang] = {}
    return catalogues


def translator(language: str = "en") -> Translator:
    return Translator(language, _load_catalogues())


def reload_catalogues() -> None:
    _load_catalogues.cache_clear()
