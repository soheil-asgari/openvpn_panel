"""Tests for the i18n catalogue loader."""

from __future__ import annotations

from app.core.i18n import SUPPORTED_LANGUAGES, translator


def test_english_lookup() -> None:
    t = translator("en")
    assert t("login.title") == "Sign in"
    assert not t.is_rtl


def test_persian_is_rtl() -> None:
    t = translator("fa")
    assert t("login.title") == "ورود"
    assert t.is_rtl


def test_missing_key_falls_back_to_key() -> None:
    t = translator("en")
    assert t("does.not.exist") == "does.not.exist"


def test_unknown_language_falls_back_to_english() -> None:
    t = translator("zz")
    assert t.language == "en"
    assert t("login.title") == "Sign in"


def test_interpolation() -> None:
    t = translator("en")
    assert "x" in t("common.{word}", word="x")


def test_catalogues_cover_all_languages() -> None:
    for lang in SUPPORTED_LANGUAGES:
        assert translator(lang).language == lang


def test_fa_and_en_have_same_keys() -> None:
    import json
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "app" / "i18n"
    en = json.loads((base / "en.json").read_text(encoding="utf-8"))
    fa = json.loads((base / "fa.json").read_text(encoding="utf-8"))
    assert set(en) == set(fa)
