"""Draft, citation, and cost helpers for the local card workspace."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid


MAX_MANUAL_CONTENT = 6000


@dataclass
class Draft:
    draft_id: str
    topic: str
    content: str
    citations: list
    risk_flags: list = field(default_factory=list)
    generated: dict = None
    approved: bool = False
    usage: dict = None
    style: str = "classic"
    color_variant: str = "black"
    image_path: str = ""

    @classmethod
    def from_manual(cls, topic, content, source_name="", source_date="", source_url="", now=None):
        topic = (topic or "").strip()
        content = (content or "").strip()
        if not topic:
            raise ValueError("主題不可空白")
        if not content:
            raise ValueError("內容不可空白")
        if len(content) > MAX_MANUAL_CONTENT:
            raise ValueError("手動內容不可超過 6000 字")
        now = now or datetime.now(timezone.utc)
        citation = {
            "name": (source_name or "使用者提供內容").strip(),
            "date": (source_date or now.date().isoformat()).strip(),
            "url": (source_url or "").strip(),
        }
        return cls(uuid.uuid4().hex, topic, content, [citation])


def estimate_cost(text, pricing, output_tokens):
    """Provide a deliberately conservative pre-request token estimate."""
    input_tokens = max(1, int(len(text or "") * 1.2) + 120)
    output_tokens = int(output_tokens)
    input_cost = input_tokens / 1000000.0 * float(pricing["input_per_million"])
    output_cost = output_tokens / 1000000.0 * float(pricing["output_per_million"])
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost": round(input_cost + output_cost, 6),
    }


def monthly_budget_available(spent, limit):
    return float(spent) < float(limit)
