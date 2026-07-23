"""
Shared streaming helper for workflow executors.

Wraps ``ChatAgent.run_stream`` so callers get the incremental token deltas
(for a live UI) while still receiving the full accumulated text at the end.
All agent calls are traced to Application Insights via OpenTelemetry.
"""

from __future__ import annotations

from typing import Callable

from agent_framework import ChatAgent, ChatMessage
from tracing import get_tracer

_tracer = get_tracer("agentflow-studio.agents")


async def stream_agent_text(
    agent: ChatAgent,
    messages: "list[ChatMessage]",
    emit_delta: Callable[[str, str], None] | None = None,
    workflow_name: str = "",
    agent_name: str = "",
) -> str:
    """
    Run ``agent`` over ``messages`` in streaming mode.

    Parameters
    ----------
    agent : ChatAgent
        The agent to run.
    messages : list[ChatMessage]
        Conversation context.
    emit_delta : callable, optional
        ``(delta, full_text) -> None`` invoked for every non-empty token chunk.
    workflow_name : str, optional
        Name of the parent workflow (for tracing).
    agent_name : str, optional
        Name of this agent step (for tracing).

    Returns
    -------
    str
        The full accumulated response text.
    """
    # Derive agent name from the ChatAgent if not provided
    span_name = agent_name or getattr(agent, "name", "unknown-agent")
    if workflow_name:
        span_name = f"{workflow_name}/{span_name}"

    input_text = messages[-1].text if messages else ""

    with _tracer.start_as_current_span(
        span_name,
        attributes={
            "agent.name": agent_name or getattr(agent, "name", "unknown"),
            "workflow.name": workflow_name or "unknown",
            "agent.input_length": len(input_text) if input_text else 0,
            "agent.input_preview": (input_text[:500] if input_text else ""),
        },
    ) as span:
        full = ""
        async for update in agent.run_stream(messages):
            delta = update.text or ""
            if delta:
                full += delta
                if emit_delta:
                    emit_delta(delta, full)

        span.set_attribute("agent.output_length", len(full))
        span.set_attribute("agent.output_preview", full[:500])
        from opentelemetry.trace import StatusCode
        span.set_status(StatusCode.OK)

    return full
