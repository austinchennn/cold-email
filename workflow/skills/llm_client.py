"""
Thin wrapper around a LangChain chat model.

All agents and skills call call_llm() / call_llm_chat() / call_llm_json()
instead of touching LangChain directly, so model, retry, and JSON-mode logic
live in one place.

Backend: langchain-openai's ChatOpenAI, pointed at the OpenAI API or — if
GEMINI_API_KEY is set — at Gemini's OpenAI-compatible endpoint. The public
function signatures are unchanged from the previous raw-SDK implementation.
"""

import json
import time
import logging
import warnings
from typing import Any, Dict, List, Optional, Tuple

# langchain-core still imports pydantic.v1 shims, which warn loudly on
# Python 3.14+. We don't touch that code path — silence just this warning.
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible",
    category=UserWarning,
)

from langchain_openai import ChatOpenAI
from openai import RateLimitError, APIConnectionError, APIStatusError

from config.settings import OPENAI_API_KEY, LLM_MODEL, LLM_TEMPERATURE, MAX_RETRIES
from config.settings import GEMINI_API_KEY
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# ChatOpenAI instances are cached by (model, temperature, json_mode) so we
# reuse the underlying HTTP client across calls.
_models: Dict[Tuple[str, float, bool], ChatOpenAI] = {}


def _get_model(model: str, temperature: float, json_mode: bool) -> ChatOpenAI:
    key = (model, temperature, json_mode)
    cached = _models.get(key)
    if cached is not None:
        return cached

    if GEMINI_API_KEY:
        # Gemini exposes an OpenAI-compatible endpoint — no extra SDK needed
        api_key, base_url = GEMINI_API_KEY, _GEMINI_BASE_URL
    elif OPENAI_API_KEY:
        api_key, base_url = OPENAI_API_KEY, None
    else:
        raise EnvironmentError(
            "No API key found. Set OPENAI_API_KEY or GEMINI_API_KEY in .env"
        )

    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "api_key": api_key,
        # max_retries=0: disable LangChain/SDK built-in retries, let our loop handle it
        "max_retries": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if json_mode:
        # The system_prompt MUST mention "Return JSON" for the model to comply reliably.
        kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}

    chat_model = ChatOpenAI(**kwargs)
    _models[key] = chat_model
    return chat_model


def _content_to_str(content: Any) -> str:
    """Coerce a LangChain message .content (str or list of blocks) to a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content or "")


def _run(
    messages: List[Dict[str, str]],
    *,
    json_mode: bool,
    model: Optional[str],
    temperature: Optional[float],
    agent_id: int,
    step: str,
) -> str:
    """Shared execution path for call_llm() and call_llm_chat()."""
    model_name = model or LLM_MODEL
    temp = temperature if temperature is not None else LLM_TEMPERATURE
    chat_model = _get_model(model_name, temp, json_mode)

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
        data={"step": step, "system": system, "user": last_user, "model": model_name},
    ))

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = chat_model.invoke(messages)
            content = _content_to_str(response.content)
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
    return _run(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        json_mode=json_mode,
        model=model,
        temperature=temperature,
        agent_id=agent_id,
        step=step,
    )


def call_llm_chat(
    messages: List[Dict[str, str]],
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
    return _run(
        list(messages),
        json_mode=json_mode,
        model=model,
        temperature=temperature,
        agent_id=agent_id,
        step=step,
    )


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
