"""
Guardrails Middleware for AgentFlow Studio
==========================================
Provides a ChatMiddleware that checks both input messages and model
responses against a configurable blocklist.  Terms can be matched by
exact (case-insensitive) string or by regex pattern.

The blocklist is loaded from a CSV file with columns ``Term`` and ``Type``
(same format accepted by the Azure AI Foundry Guardrails portal).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from agent_framework import ChatMessage, ChatMiddleware, ChatResponse, Role
from agent_framework._middleware import ChatContext


# ---------------------------------------------------------------------------
# Blocklist loader
# ---------------------------------------------------------------------------
def load_blocklist(csv_path: str | Path) -> list[dict[str, Any]]:
    """Load a blocklist CSV (Term, Type) and return a list of rule dicts."""
    rules: list[dict[str, Any]] = []
    path = Path(csv_path)
    if not path.exists():
        return rules
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            term = row.get("Term", "").strip()
            match_type = row.get("Type", "Exact match").strip().lower()
            if not term:
                continue
            if "regex" in match_type:
                try:
                    rules.append({"pattern": re.compile(term, re.IGNORECASE), "term": term, "type": "regex"})
                except re.error:
                    pass  # skip invalid regex
            else:
                rules.append({"pattern": re.compile(re.escape(term), re.IGNORECASE), "term": term, "type": "exact"})
    return rules


# ---------------------------------------------------------------------------
# Guardrails ChatMiddleware
# ---------------------------------------------------------------------------
class GuardrailsMiddleware(ChatMiddleware):
    """
    Intercepts chat requests to enforce a blocklist on both user input
    and model output.

    - **Input check**: scans user messages *before* calling the model.
      If a blocked term is found the request is short-circuited and a
      safe canned response is returned.
    - **Output check**: scans the model response *after* generation.
      If a blocked term is found in the assistant reply, the response
      is replaced with a safe message.

    Usage::

        mw = GuardrailsMiddleware.from_csv("blocklist.csv")
        agent = client.create_agent(name="MyAgent", middleware=mw)
    """

    def __init__(self, rules: list[dict[str, Any]] | None = None):
        self.rules = rules or []
        self._violations: list[dict[str, str]] = []

    @classmethod
    def from_csv(cls, csv_path: str | Path) -> "GuardrailsMiddleware":
        """Create a middleware instance from a blocklist CSV file."""
        return cls(rules=load_blocklist(csv_path))

    @property
    def last_violations(self) -> list[dict[str, str]]:
        """Return the list of violations detected in the last call."""
        return list(self._violations)

    # ----- core middleware logic ------------------------------------------

    async def process(self, context: ChatContext, next) -> None:
        self._violations = []

        # --- Input guardrail ------------------------------------------------
        for msg in context.messages:
            text = _extract_text(msg)
            if not text:
                continue
            violations = self._scan(text)
            if violations:
                self._violations.extend(violations)
                blocked_terms = ", ".join(v["term"] for v in violations)
                context.result = ChatResponse(
                    text=(
                        f"⚠️ **Guardrail triggered** — your message was blocked because "
                        f"it contains restricted content ({blocked_terms}). "
                        f"Please rephrase your request without prohibited terms."
                    ),
                )
                context.terminate = True
                return

        # --- Call the model --------------------------------------------------
        await next(context)

        # --- Output guardrail -----------------------------------------------
        if context.result is not None:
            response_text = _extract_response_text(context.result)
            if response_text:
                violations = self._scan(response_text)
                if violations:
                    self._violations.extend(violations)
                    blocked_terms = ", ".join(v["term"] for v in violations)
                    context.result = ChatResponse(
                        text=(
                            f"⚠️ **Guardrail triggered** — the model's response was "
                            f"blocked because it contains restricted content ({blocked_terms}). "
                            f"The response has been suppressed for safety."
                        ),
                    )

    # ----- helpers -----------------------------------------------------------

    def _scan(self, text: str) -> list[dict[str, str]]:
        """Return a list of {term, type, match} dicts for every blocklist hit."""
        hits: list[dict[str, str]] = []
        for rule in self.rules:
            m = rule["pattern"].search(text)
            if m:
                hits.append({"term": rule["term"], "type": rule["type"], "match": m.group()})
        return hits


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------
def _extract_text(msg: ChatMessage) -> str:
    """Get the plain-text content of a ChatMessage."""
    if hasattr(msg, "text") and msg.text:
        return msg.text
    if hasattr(msg, "contents") and msg.contents:
        parts = []
        for part in msg.contents:
            if hasattr(part, "text"):
                parts.append(part.text)
        return " ".join(parts)
    return ""


def _extract_response_text(result) -> str:
    """Extract text from a ChatResponse (non-streaming)."""
    if hasattr(result, "text") and result.text:
        return result.text if isinstance(result.text, str) else getattr(result.text, "text", "")
    if hasattr(result, "messages"):
        parts = []
        for msg in result.messages:
            parts.append(_extract_text(msg))
        return " ".join(parts)
    return ""
