from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import retry, stop_after_attempt, wait_fixed

from bidpilot_data.logging import get_logger
from bidpilot_data.settings import get_settings

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def repair_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = JSON_BLOCK_RE.search(text)
        if not m:
            raise
        candidate = m.group(0)
        candidate = candidate.replace("“", '"').replace("”", '"').replace("'", '"')
        return json.loads(candidate)


class OpenAICompatibleClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openai_api_key
        self.base_url = settings.openai_base_url.rstrip("/")
        self.model = settings.resolved_model_name()

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.api_key != "sk-replace-me")

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
    def chat_json(
        self,
        *,
        system: str,
        user: str,
        schema_model: type[T],
        temperature: float = 0.0,
    ) -> tuple[T, dict[str, Any]]:
        if not self.available:
            raise RuntimeError("OPENAI_API_KEY not configured for LLM mode")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        try:
            obj = repair_json(content)
            parsed = schema_model.model_validate(obj)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            log.warning("json parse failed, retrying repair path: %s", exc)
            obj = repair_json(content)
            parsed = schema_model.model_validate(obj)
        meta = {
            "model_name": self.model,
            "temperature": temperature,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "raw": content,
        }
        return parsed, meta
