"""User settings utility functions."""

from typing import Optional


def get_user_locale(user) -> str:
    """Get user's locale preference from settings.

    Args:
        user: User object with optional settings attribute

    Returns:
        Locale string (e.g., "en-GB", "fr-CA") or empty string if not set
    """
    if user and hasattr(user, "settings") and user.settings:
        try:
            ui = user.settings.model_dump().get("ui", {}) or {}
            return ui.get("default_locale", "")
        except Exception:
            pass
    return ""


def get_search_lang(locale: str) -> str:
    """Convert locale to search language code.

    Args:
        locale: Locale string (e.g., "en-GB", "fr-CA")

    Returns:
        Two-letter language code for search APIs (e.g., "en", "fr")
    """
    if not locale:
        return "en"

    lang_code = locale.split("-")[0].lower()

    if lang_code.startswith("fr"):
        return "fr"
    elif lang_code.startswith("en"):
        return "en"

    return lang_code


def get_current_lang_description(locale: str) -> str:
    """Get human-readable language description for system prompts.

    Args:
        locale: Locale string (e.g., "en-GB", "fr-CA")

    Returns:
        Human-readable language string for context prompts (e.g., "English", "French (Français)")
    """
    if not locale:
        return "English"

    lang_code = locale.split("-")[0].lower()

    if lang_code.startswith("fr"):
        return "French (Français)"
    elif lang_code.startswith("en"):
        return "English"

    return lang_code.title()
