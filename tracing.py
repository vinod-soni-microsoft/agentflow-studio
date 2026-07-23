"""
Application Insights tracing for all workflow agents.
Configures OpenTelemetry with Azure Monitor exporter to capture agent traces,
spans, and custom events for every workflow execution.
"""

import logging
import os

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace
from opentelemetry.trace import StatusCode

logger = logging.getLogger(__name__)

_initialized = False


def init_tracing() -> None:
    """Initialize Application Insights tracing via OpenTelemetry.

    Requires APPLICATIONINSIGHTS_CONNECTION_STRING in environment or .env.
    Call once at app startup.
    """
    global _initialized
    if _initialized:
        return

    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    if not connection_string:
        logger.warning(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set — "
            "Application Insights tracing is disabled."
        )
        return

    configure_azure_monitor(connection_string=connection_string)
    _initialized = True
    logger.info("Application Insights tracing initialized.")


def get_tracer(name: str = "agentflow-studio") -> trace.Tracer:
    """Return a named OpenTelemetry tracer."""
    return trace.get_tracer(name)


def trace_agent_step(workflow_name: str, agent_name: str, input_text: str, output_text: str) -> None:
    """Record a single agent step as a span in Application Insights."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        f"{workflow_name}/{agent_name}",
        attributes={
            "workflow.name": workflow_name,
            "agent.name": agent_name,
            "agent.input_length": len(input_text),
            "agent.output_length": len(output_text),
        },
    ) as span:
        span.set_attribute("agent.input", input_text[:1000])
        span.set_attribute("agent.output", output_text[:1000])
        span.set_status(StatusCode.OK)


def trace_workflow_start(workflow_name: str, input_text: str):
    """Start a workflow-level span. Returns the span context manager."""
    tracer = get_tracer()
    span = tracer.start_span(
        f"workflow/{workflow_name}",
        attributes={
            "workflow.name": workflow_name,
            "workflow.input": input_text[:1000],
        },
    )
    return span


def trace_workflow_end(span, workflow_name: str, success: bool = True, error: str = "") -> None:
    """End a workflow-level span."""
    if span is None:
        return
    if success:
        span.set_status(StatusCode.OK)
    else:
        span.set_status(StatusCode.ERROR, error)
        span.set_attribute("workflow.error", error[:1000])
    span.end()
