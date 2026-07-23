import os


class PaidServiceDisabled(ValueError):
    pass


def paid_ai_enabled(environ=None):
    values = os.environ if environ is None else environ
    provider = values.get("AI_PROVIDER", "disabled").strip().lower()
    api_key = values.get("OPENAI_API_KEY", "").strip()
    return provider == "openai" and bool(api_key)


def require_paid_ai_enabled(environ=None):
    if not paid_ai_enabled(environ):
        raise PaidServiceDisabled(
            "AI 功能目前停用；必須先取得費用確認並明確啟用 AI_PROVIDER=openai。"
        )
