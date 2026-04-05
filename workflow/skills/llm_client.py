"""
Thin wrapper around the OpenAI Chat Completions API.

All agents and skills call call_llm() / call_llm_json() instead of
touching the SDK directly, so model, retry, and JSON-mode logic
live in one place.
"""

import json
import time
import logging
from typing import Any, Dict, Optional

from openai import OpenAI, RateLimitError, APIConnectionError, APIStatusError

from config.settings import OPENAI_API_KEY, LLM_MODEL, LLM_TEMPERATURE, MAX_RETRIES
from config.settings import GEMINI_API_KEY
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if GEMINI_API_KEY:
            # Gemini exposes an OpenAI-compatible endpoint — no extra SDK needed
            # max_retries=0: disable SDK built-in retries, let our logic handle it
            _client = OpenAI(
                api_key=GEMINI_API_KEY,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                max_retries=0,
            )
        elif OPENAI_API_KEY:
            _client = OpenAI(api_key=OPENAI_API_KEY, max_retries=0)
        else:
            raise EnvironmentError(
                "No API key found. Set OPENAI_API_KEY or GEMINI_API_KEY in .env"
            )
    return _client


def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    agent_id: int = 0,
    step: str = "",
) -> str:
    """
    Send a chat completion request and return the assistant reply as a string.

    Parameters
    ----------
    json_mode : bool
        If True, enables JSON output mode. The system_prompt MUST mention
        "Return JSON" for the model to comply reliably.
    """
    client = _get_client()
    kwargs: Dict[str, Any] = {
        "model": model or LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    bus.post(Event(
        type=EventType.LLM_CALL,
        agent_id=agent_id,
        data={
            "step":   step,
            "system": system_prompt[:800],
            "user":   user_prompt[:800],
            "model":  model or LLM_MODEL,
        },
    ))

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            bus.post(Event(
                type=EventType.LLM_RESPONSE,
                agent_id=agent_id,
                data={"step": step, "response": content[:2000]},
            ))
            return content
        except RateLimitError:
            # Gemini free tier needs longer waits; use 60s base for 429s
            wait = 60 if GEMINI_API_KEY else 2 ** attempt
            logger.warning(
                f"Rate limited — retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(wait)
        except APIConnectionError as exc:
            logger.error(f"Connection error: {exc}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2)
        except APIStatusError as exc:
            logger.error(f"API error {exc.status_code}: {exc.message}")
            raise

    raise RuntimeError("LLM call failed after all retries.")


def call_llm_chat(
    messages: list[Dict[str, str]],
    *,
    json_mode: bool = False,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    agent_id: int = 0,
    step: str = "",
) -> str:
    """
    Multi-turn chat completion — accepts a full message list.

    Parameters
    ----------
    messages : list of {"role": ..., "content": ...} dicts
    """
    client = _get_client()
    kwargs: Dict[str, Any] = {
        "model": model or LLM_MODEL,
        "messages": messages,
        "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_user = ""
    for m in reversed(messages):
        if m["role"] == "user":
            last_user = m["content"][:800]
            break
    system = ""
    for m in messages:
        if m["role"] == "system":
            system = m["content"][:800]
            break

    bus.post(Event(
        type=EventType.LLM_CALL,
        agent_id=agent_id,
        data={"step": step, "system": system, "user": last_user,
              "model": model or LLM_MODEL},
    ))

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            bus.post(Event(
                type=EventType.LLM_RESPONSE,
                agent_id=agent_id,
                data={"step": step, "response": content[:2000]},
            ))
            return content
        except RateLimitError:
            wait = 60 if GEMINI_API_KEY else 2 ** attempt
            logger.warning(
                f"Rate limited — retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})"
            )
            time.sleep(wait)
        except APIConnectionError as exc:
            logger.error(f"Connection error: {exc}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2)
        except APIStatusError as exc:
            logger.error(f"API error {exc.status_code}: {exc.message}")
            raise

    raise RuntimeError("LLM chat call failed after all retries.")


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    **kwargs,
) -> Dict:
    """Convenience wrapper: calls LLM in JSON mode and returns parsed dict."""
    agent_id = kwargs.pop("agent_id", 0)
    step     = kwargs.pop("step",     "")
    raw = call_llm(system_prompt, user_prompt, json_mode=True,
                   agent_id=agent_id, step=step, **kwargs)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            f"Failed to parse LLM JSON output: {exc}\nRaw output:\n{raw[:500]}"
        )
        raise
