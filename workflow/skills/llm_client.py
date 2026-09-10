"""
LangChain chat-model access layer.

Everything the agents and skills need to talk to an LLM lives here:

  get_chat_model()      → a configured, cached ChatOpenAI (build your own LCEL chain)
  run_chain()           → invoke any Runnable with the shared retry loop + dashboard events
  call_llm()            → one-shot system+user prompt, returns str
  call_llm_chat()       → multi-turn message list, returns str
  call_llm_json()       → call_llm in JSON mode, returns parsed dict
  call_llm_structured() → returns a validated Pydantic model via with_structured_output()

Backend: langchain-openai's ChatOpenAI, pointed at the OpenAI API or — when
GEMINI_API_KEY is set — at Gemini's OpenAI-compatible endpoint. Retry logic
(429 long-wait, connection backoff) is implemented here, so the models are
built with max_retries=0.
"""

import json
import time
import logging
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from openai import RateLimitError, APIConnectionError, APIStatusError

from config.settings import OPENAI_API_KEY, LLM_MODEL, LLM_TEMPERATURE, MAX_RETRIES
from config.settings import GEMINI_API_KEY
from skills.llm_events import BusCallbackHandler

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

TModel = TypeVar("TModel", bound=BaseModel)

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


def get_chat_model(
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    json_mode: bool = False,
) -> ChatOpenAI:
    """Return a cached ChatOpenAI. Compose it into an LCEL chain, then hand the
    chain to run_chain() so it gets the shared retry loop and dashboard events."""
    return _get_model(
        model or LLM_MODEL,
        temperature if temperature is not None else LLM_TEMPERATURE,
        json_mode,
    )


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


def run_chain(
    chain: Runnable,
    chain_input: Any,
    *,
    agent_id: int = 0,
    step: str = "",
) -> Any:
    """
    Invoke any LCEL Runnable, retrying transient API failures and posting
    LLM_CALL / LLM_RESPONSE events for the dashboard.

    Retry policy (unchanged from the raw-SDK version):
      - RateLimitError      → wait 60s (Gemini free tier) or 2**attempt, then retry
      - APIConnectionError  → wait 2s and retry, re-raise on the last attempt
      - APIStatusError      → re-raise immediately
    """
    config = {"callbacks": [BusCallbackHandler(agent_id, step)]}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return chain.invoke(chain_input, config=config)
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

    raise RuntimeError("LLM call failed after all retries.")


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
    chat_model = get_chat_model(
        model=model, temperature=temperature, json_mode=json_mode
    )
    reply = run_chain(chat_model, messages, agent_id=agent_id, step=step)
    return _content_to_str(reply.content)


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


def call_llm_structured(
    system_prompt: str,
    user_prompt: str,
    schema: Type[TModel],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    agent_id: int = 0,
    step: str = "",
) -> TModel:
    """
    Ask the model to fill in `schema` (a Pydantic model) and return a validated
    instance. Uses ChatOpenAI.with_structured_output() under the hood, so the
    provider enforces the JSON schema instead of relying on prompt wording.
    """
    chat_model = get_chat_model(model=model, temperature=temperature)
    chain = chat_model.with_structured_output(schema)
    return run_chain(
        chain,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        agent_id=agent_id,
        step=step,
    )
