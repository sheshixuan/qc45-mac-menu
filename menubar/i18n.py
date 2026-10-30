"""Two-language text for the menu. No extra packages."""
from __future__ import annotations

import os

SUPPORTED = ("en", "zh-Hans")
_lang = "en"


def current_language() -> str:
    return _lang


def language_file() -> str:
    override = os.environ.get("QC45_LANG_FILE")
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser("~/.qc45/ui-language")


def set_language(lang: str, persist: bool = False) -> str:
    global _lang
    _lang = lang if lang in SUPPORTED else "en"
    if persist:
        path = language_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_lang + "\n")
    return _lang


def detect_language() -> str:
    try:
        from Foundation import NSLocale
        prefs = NSLocale.preferredLanguages()
        first = str(prefs[0]) if len(prefs) else "en"
    except Exception:
        first = "en"
    return "zh-Hans" if first.lower().startswith("zh") else "en"


def load_language() -> str:
    try:
        with open(language_file(), encoding="utf-8") as handle:
            saved = handle.read().strip()
    except Exception:
        saved = ""
    if saved in SUPPORTED:
        return set_language(saved)
    return set_language(detect_language())


def T(zh: str, en: str) -> str:
    return zh if _lang == "zh-Hans" else en
