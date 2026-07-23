"""
Sequential Workflow — Customer Support Ticket Triage
=====================================================
Real-world use case: An incoming customer support ticket is processed through
three agents in strict order:
  1. **Classifier** — Categorizes the ticket (billing, technical, general).
  2. **Researcher** — Looks up relevant knowledge-base articles for the category.
  3. **Responder** — Drafts a polished customer-facing reply.

Each agent's output feeds directly into the next, demonstrating a classic
sequential (pipeline) pattern built with the Microsoft Agent Framework.
"""

import asyncio
from typing import Any

from agent_framework import (
    ChatAgent,
    ChatMessage,
    Executor,
    Role,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
    WorkflowRunState,
    handler,
)
from agent_framework.azure import AzureAIClient
from azure.identity.aio import DefaultAzureCredential

from config import FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL_DEPLOYMENT_NAME
from workflows.streaming import stream_agent_text
from tracing import get_tracer, trace_workflow_end


# ---------------------------------------------------------------------------
# Executor 1 — Classifier
# ---------------------------------------------------------------------------
class ClassifierExecutor(Executor):
    """Categorizes the incoming support ticket."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, emit=None, id: str = "classifier"):
        self.agent = agent
        self._emit = emit
        super().__init__(id=id)

    @handler
    async def handle(self, message: ChatMessage, ctx: WorkflowContext[list[ChatMessage]]) -> None:
        messages = [message]
        text = await _run_step(self.agent, messages, self.id, self._emit)
        messages.append(ChatMessage(role=Role.ASSISTANT, text=text))
        await ctx.send_message(messages)


# ---------------------------------------------------------------------------
# Executor 2 — Researcher
# ---------------------------------------------------------------------------
class ResearcherExecutor(Executor):
    """Finds relevant knowledge-base information for the ticket category."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, emit=None, id: str = "researcher"):
        self.agent = agent
        self._emit = emit
        super().__init__(id=id)

    @handler
    async def handle(self, messages: list[ChatMessage], ctx: WorkflowContext[list[ChatMessage]]) -> None:
        text = await _run_step(self.agent, messages, self.id, self._emit)
        messages.append(ChatMessage(role=Role.ASSISTANT, text=text))
        await ctx.send_message(messages)


# ---------------------------------------------------------------------------
# Executor 3 — Responder
# ---------------------------------------------------------------------------
class ResponderExecutor(Executor):
    """Drafts a customer-facing support reply."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, emit=None, id: str = "responder"):
        self.agent = agent
        self._emit = emit
        super().__init__(id=id)

    @handler
    async def handle(self, messages: list[ChatMessage], ctx: WorkflowContext[Any, str]) -> None:
        text = await _run_step(self.agent, messages, self.id, self._emit)
        await ctx.yield_output(text)


# ---------------------------------------------------------------------------
# Streaming helper — emits live token deltas per executor step
# ---------------------------------------------------------------------------
async def _run_step(agent: ChatAgent, messages: list[ChatMessage], step_id: str, emit) -> str:
    """Stream an agent step, emitting start / delta / complete events."""
    if emit:
        emit({"type": "agent_step_start", "agent": step_id, "content": ""})

    def _on_delta(delta: str, full: str) -> None:
        if emit:
            emit({"type": "agent_delta", "agent": step_id, "delta": delta, "content": full})

    text = await stream_agent_text(agent, messages, _on_delta, workflow_name="sequential", agent_name=step_id)

    if emit:
        emit({"type": "agent_step_complete", "agent": step_id, "content": text})
    return text


# ---------------------------------------------------------------------------
# Public API — called from the Streamlit UI
# ---------------------------------------------------------------------------
async def run_sequential_workflow(
    ticket_text: str,
    on_event=None,
    classifier_instructions: str | None = None,
    researcher_instructions: str | None = None,
    responder_instructions: str | None = None,
):
    """
    Execute the sequential pipeline and return a list of event dicts
    suitable for rendering in the UI.

    Parameters
    ----------
    ticket_text : str
        The raw customer support ticket text.
    on_event : callable, optional
        An optional callback ``(event_dict) -> None`` invoked per event.
    classifier_instructions : str, optional
        Custom instructions for the Classifier agent.
    researcher_instructions : str, optional
        Custom instructions for the Researcher agent.
    responder_instructions : str, optional
        Custom instructions for the Responder agent.

    Returns
    -------
    list[dict]
        A list of event dictionaries with keys ``type``, ``agent``, ``content``.
    """
    # Defaults
    if not classifier_instructions:
        classifier_instructions = (
            "You are a customer-support ticket classifier. "
            "Read the customer ticket and respond with EXACTLY one category "
            "(Billing, Technical, or General) followed by a one-sentence reason. "
            "Format: 'Category: <category>\\nReason: <reason>'"
        )
    if not researcher_instructions:
        researcher_instructions = (
            "You are a knowledge-base researcher for a support team. "
            "Given the ticket and its classification, provide 2-3 bullet points "
            "of relevant knowledge-base information that would help draft a reply. "
            "Be concise and factual."
        )
    if not responder_instructions:
        responder_instructions = (
            "You are a professional customer-support agent. "
            "Using the ticket, classification, and knowledge-base notes provided, "
            "draft a friendly, empathetic, and helpful reply to the customer. "
            "Keep it under 150 words."
        )

    events_log: list[dict] = []
    _wf_tracer = get_tracer("agentflow-studio.workflows")
    _wf_span = _wf_tracer.start_span("workflow/sequential", attributes={"workflow.name": "sequential", "workflow.input": ticket_text[:500]})

    async with DefaultAzureCredential() as credential:
        client_kwargs = dict(
            project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
            model_deployment_name=FOUNDRY_MODEL_DEPLOYMENT_NAME,
            credential=credential,
        )

        async with (
            AzureAIClient(**client_kwargs).create_agent(
                name="TicketClassifier",
                instructions=classifier_instructions,
            ) as classifier_agent,
            AzureAIClient(**client_kwargs).create_agent(
                name="KnowledgeResearcher",
                instructions=researcher_instructions,
            ) as researcher_agent,
            AzureAIClient(**client_kwargs).create_agent(
                name="SupportResponder",
                instructions=responder_instructions,
            ) as responder_agent,
        ):
            classifier = ClassifierExecutor(classifier_agent, emit=on_event)
            researcher = ResearcherExecutor(researcher_agent, emit=on_event)
            responder = ResponderExecutor(responder_agent, emit=on_event)

            workflow = (
                WorkflowBuilder()
                .add_edge(classifier, researcher)
                .add_edge(researcher, responder)
                .set_start_executor(classifier)
                .build()
            )

            user_msg = ChatMessage(role=Role.USER, text=ticket_text)

            async for event in workflow.run_stream(user_msg):
                entry: dict = {}
                if isinstance(event, WorkflowStatusEvent):
                    entry = {
                        "type": "status",
                        "agent": "workflow",
                        "content": str(event.state),
                    }
                elif isinstance(event, WorkflowOutputEvent):
                    entry = {
                        "type": "output",
                        "agent": "responder",
                        "content": event.data,
                    }
                else:
                    evt_name = event.__class__.__name__
                    executor_id = getattr(event, "executor_id", "")
                    entry = {
                        "type": evt_name,
                        "agent": executor_id,
                        "content": str(event),
                    }

                if entry:
                    events_log.append(entry)
                    if on_event:
                        on_event(entry)

    trace_workflow_end(_wf_span, "sequential", success=True)
    return events_log


# ---------------------------------------------------------------------------
# Stand-alone CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sample_ticket = (
        "Hi, I was charged twice for my subscription last month. "
        "Order #12345. Please help me get a refund."
    )

    async def _main():
        results = await run_sequential_workflow(
            sample_ticket,
            on_event=lambda e: print(f"[{e['type']}] {e['agent']}: {e['content']}")
        )
        print("\n--- Final reply ---")
        for r in results:
            if r["type"] == "output":
                print(r["content"])

    asyncio.run(_main())
