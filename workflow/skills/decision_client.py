"""
jev decision client
===================
Thin wrapper around TypeSafe's jev "System One" model: given a state and a set
of labelled options, it returns the chosen option plus a probability for every
option — no free-form text, so it is fast and cheap enough for routing.

  decide() → Decision(choice, confidence, probabilities), or None whenever the
             caller should fall back to the regular LLM:
               - TYPESAFE_API_KEY is not set
               - the request failed
               - jev's confidence is below DECISION_MIN_CONFIDENCE

Returning None instead of raising keeps jev strictly optional: a missing key or
an outage degrades to the old LLM-only behaviour.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from langchain_typesafe import Choice, TypeSafeClassifier

from config.settings import DECISION_MIN_CONFIDENCE, DECISION_MODEL, TYPESAFE_API_KEY
from skills.event_bus import bus, Event, EventType

logger = logging.getLogger(__name__)

# One classifier per question name so the underlying HTTP clients are pooled.
_classifiers: Dict[str, TypeSafeClassifier] = {}


@dataclass(frozen=True)
class Decision:
    choice: str
    confidence: float
    probabilities: Dict[str, float]


def _get_classifier(
    name: str, instructions: str, options: Dict[str, str]
) -> TypeSafeClassifier:
    classifier = _classifiers.get(name)
    if classifier is None:
        classifier = TypeSafeClassifier(
            questions={name: Choice(instructions=instructions, criteria=options)},
            model=DECISION_MODEL,
            api_key=TYPESAFE_API_KEY,
        )
        _classifiers[name] = classifier
    return classifier


def decide(
    name: str,
    instructions: str,
    options: Dict[str, str],
    state: Any,
    *,
    agent_id: int = 0,
    step: str = "",
) -> Optional[Decision]:
    """
    Ask jev to pick one of `options` for `state`.

    Parameters
    ----------
    name         : question id (also the cache key for the classifier)
    instructions : the complete judgment jev should make
    options      : {label: description}; jev returns one of these labels
    state        : text, JSON, or LangChain messages describing the situation
    """
    if not TYPESAFE_API_KEY:
        return None

    bus.post(Event(
        type=EventType.LLM_CALL,
        agent_id=agent_id,
        data={
            "step": step,
            "system": instructions[:800],
            "user": str(state)[-800:],
            "model": DECISION_MODEL,
        },
    ))

    try:
        response = _get_classifier(name, instructions, options).invoke(state)
        answer = response.choices[name]
    except Exception as exc:  # any jev failure must degrade to the LLM path
        logger.warning(f"jev decision '{name}' failed, falling back to LLM: {exc}")
        return None

    bus.post(Event(
        type=EventType.LLM_RESPONSE,
        agent_id=agent_id,
        data={
            "step": step,
            "response": (
                f"{answer.choice} (confidence {answer.confidence:.2f}) "
                f"{answer.probabilities}"
            ),
        },
    ))

    if answer.choice not in options:
        logger.warning(f"jev returned unknown option {answer.choice!r}; ignoring")
        return None
    if answer.confidence < DECISION_MIN_CONFIDENCE:
        logger.info(
            f"jev '{name}' confidence {answer.confidence:.2f} < "
            f"{DECISION_MIN_CONFIDENCE}; falling back to LLM"
        )
        return None

    return Decision(answer.choice, answer.confidence, dict(answer.probabilities))
