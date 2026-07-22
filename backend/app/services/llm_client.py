"""OpenAI-compatible LLM client for vLLM (Qwen3-8B).

Never logs full document content or API keys. Thinking is disabled for Qwen3.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("bidpilot.llm")


class LlmError(Exception):
    """Base class for LLM client failures."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message


class LlmDisabledError(LlmError):
    pass


class LlmUnavailableError(LlmError):
    pass


class LlmTimeoutError(LlmError):
    pass


class LlmResponseError(LlmError):
    pass


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    latency_ms: float
    finish_reason: str | None
    request_id: str
    usage: dict[str, Any] | None = None


def _auth_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _qwen_extra_body() -> dict[str, Any]:
    # Qwen3 thinking mode must stay off for user-facing answers.
    return {"chat_template_kwargs": {"enable_thinking": False}}


def _strip_thinking(text: str) -> str:
    """Drop residual <think> blocks if the server still emits them."""
    import re

    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<think>[\s\S]*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


class LlmClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.llm_enabled if enabled is None else enabled
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds
        )
        self.max_tokens = settings.llm_max_tokens
        self.temperature = settings.llm_temperature

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise LlmDisabledError(
                "大模型问答未启用",
                detail="请设置 LLM_ENABLED=true 并启动 vLLM（见 scripts/serve_qwen3_vllm.sh）",
            )

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        request_id: str | None = None,
    ) -> ChatResult:
        self._ensure_enabled()
        rid = request_id or str(uuid.uuid4())
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": False,
            **_qwen_extra_body(),
        }
        url = f"{self.base_url}/chat/completions"
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload, headers=_auth_headers(self.api_key))
        except httpx.TimeoutException as exc:
            logger.warning("LLM timeout request_id=%s model=%s", rid, self.model)
            raise LlmTimeoutError(
                "大模型响应超时",
                detail=f"超过 {self.timeout_seconds:.0f}s（request_id={rid}）",
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("LLM connection failed request_id=%s: %s", rid, type(exc).__name__)
            raise LlmUnavailableError(
                "大模型服务不可用",
                detail=f"无法连接 {self.base_url}（request_id={rid}）: {exc}",
            ) from exc

        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            detail = response.text[:500]
            logger.warning(
                "LLM HTTP %s request_id=%s model=%s latency_ms=%.0f",
                response.status_code,
                rid,
                self.model,
                latency_ms,
            )
            raise LlmUnavailableError(
                "大模型服务返回错误",
                detail=f"HTTP {response.status_code}（request_id={rid}）: {detail}",
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
            content = message.get("content")
            if content is None:
                raise KeyError("choices[0].message.content")
            content = _strip_thinking(str(content))
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmResponseError(
                "大模型响应格式无效",
                detail=f"request_id={rid}: {exc}",
            ) from exc

        logger.info(
            "LLM ok request_id=%s model=%s latency_ms=%.0f finish=%s",
            rid,
            self.model,
            latency_ms,
            finish_reason,
        )
        return ChatResult(
            content=content,
            model=str(data.get("model") or self.model),
            latency_ms=round(latency_ms, 2),
            finish_reason=finish_reason,
            request_id=rid,
            usage=usage if isinstance(usage, dict) else None,
        )

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        request_id: str | None = None,
    ) -> Iterator[str]:
        """Yield text deltas. Raises LlmError on failure before/during stream."""
        self._ensure_enabled()
        rid = request_id or str(uuid.uuid4())
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": True,
            **_qwen_extra_body(),
        }
        url = f"{self.base_url}/chat/completions"
        started = time.perf_counter()
        try:
            with (
                httpx.Client(timeout=self.timeout_seconds) as client,
                client.stream(
                    "POST", url, json=payload, headers=_auth_headers(self.api_key)
                ) as response,
            ):
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")[:500]
                    raise LlmUnavailableError(
                        "大模型服务返回错误",
                        detail=f"HTTP {response.status_code}（request_id={rid}）: {body}",
                    )
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                    else:
                        continue
                    if data_str == "[DONE]":
                        break
                    import json

                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield str(piece)
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
        except LlmError:
            raise
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError(
                "大模型响应超时",
                detail=f"超过 {self.timeout_seconds:.0f}s（request_id={rid}）",
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmUnavailableError(
                "大模型服务不可用",
                detail=f"无法连接 {self.base_url}（request_id={rid}）: {exc}",
            ) from exc
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "LLM stream finished request_id=%s model=%s latency_ms=%.0f",
                rid,
                self.model,
                latency_ms,
            )

    def health_check(self) -> dict[str, Any]:
        """Probe /models without claiming the chat model is warm."""
        settings = get_settings()
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "model": self.model,
            "base_url": self.base_url,
            "reachable": False,
            "detail": None,
            "latency_ms": None,
            "status": "disabled",
        }
        if not self.enabled:
            result["detail"] = "LLM_ENABLED=false"
            return result
        url = f"{self.base_url}/models"
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=min(10.0, self.timeout_seconds)) as client:
                response = client.get(url, headers=_auth_headers(self.api_key))
            latency_ms = (time.perf_counter() - started) * 1000
            result["latency_ms"] = round(latency_ms, 2)
            if response.status_code >= 400:
                result["status"] = "error"
                result["detail"] = f"HTTP {response.status_code}"
                return result
            data = response.json()
            ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
            result["reachable"] = True
            if self.model in ids or not ids:
                result["status"] = "ok"
                result["detail"] = f"models={ids[:5]}"
            else:
                result["status"] = "error"
                result["detail"] = f"已连通但未找到 served model {self.model!r}；当前: {ids[:8]}"
        except Exception as exc:  # noqa: BLE001 - report real connectivity
            result["status"] = "error"
            result["detail"] = f"{type(exc).__name__}: {exc}"
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        # Never echo settings.llm_api_key
        _ = settings
        return result


def get_llm_client() -> LlmClient:
    return LlmClient()
