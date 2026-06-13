"""Tiny LLM helper for OCR parsing.

Wraps the OpenAI client used by ``comms_checker`` and exposes a single
``extract_json(prompt)`` function. The OCR module passes a function of
this shape to its ``parse_*_from_text`` helpers.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from config.settings import get_settings

logger = logging.getLogger(__name__)


def make_llm_extract_fn() -> "callable[[str], dict[str, Any]]":
    """Return a function ``(prompt: str) -> dict`` that calls the configured LLM.

    Uses the same OpenAI-compatible client and settings as the comms checker.
    """
    cfg = get_settings()
    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot use OCR + LLM parsing")

    client = OpenAI(
        api_key=cfg.openai_api_key,
        timeout=cfg.openai_timeout_secs,
        max_retries=2,
        base_url=cfg.openai_base_url or None,
    )
    model = cfg.openai_model

    def extract_json(prompt: str) -> dict[str, Any]:
        response = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            timeout=cfg.openai_timeout_secs,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise document parser. Return only valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.choices[0].message.content or ""
        raw = raw.strip()
        # Strip code fences if present
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        return json.loads(raw)

    return extract_json
