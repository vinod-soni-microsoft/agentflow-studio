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


# ---------------------------------------------------------------------------
# Executor 1 — Classifier
# ---------------------------------------------------------------------------
class ClassifierExecutor(Executor):
    """Categorizes the incoming support ticket."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, id: str = "classifier"):
        self.agent = agent
        super().__init__(id=id)

    @handler
    async def handle(self, message: ChatMessage, ctx: WorkflowContext[list[ChatMessage]]) -> None:
        messages = [message]
        response = await self.agent.run(messages)
        messages.extend(response.messages)
        await ctx.send_message(messages)


# ---------------------------------------------------------------------------
# Executor 2 — Researcher
# ---------------------------------------------------------------------------
class ResearcherExecutor(Executor):
    """Finds relevant knowledge-base information for the ticket category."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, id: str = "researcher"):
        self.agent = agent
        super().__init__(id=id)

    @handler
    async def handle(self, messages: list[ChatMessage], ctx: WorkflowContext[list[ChatMessage]]) -> None:
        response = await self.agent.run(messages)
        messages.extend(response.messages)
        await ctx.send_message(messages)


# ---------------------------------------------------------------------------
# Executor 3 — Responder
# ---------------------------------------------------------------------------
class ResponderExecutor(Executor):
    """Drafts a customer-facing support reply."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, id: str = "responder"):
        self.agent = agent
        super().__init__(id=id)

    @handler
    async def handle(self, messages: list[ChatMessage], ctx: WorkflowContext[Any, str]) -> None:
        response = await self.agent.run(messages)
        await ctx.yield_output(response.text)


# ---------------------------------------------------------------------------
# Public API — called from the Streamlit UI
# ---------------------------------------------------------------------------
async def run_sequential_workflow(ticket_text: str, on_event=None):
    """
    Execute the sequential pipeline and return a list of event dicts
    suitable for rendering in the UI.

    Parameters
    ----------
    ticket_text : str
        The raw customer support ticket text.
    on_event : callable, optional
        An optional callback ``(event_dict) -> None`` invoked per event.

    Returns
    -------
    list[dict]
        A list of event dictionaries with keys ``type``, ``agent``, ``content``.
    """
    events_log: list[dict] = []

    async with DefaultAzureCredential() as credential:
        client_kwargs = dict(
            project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
            model_deployment_name=FOUNDRY_MODEL_DEPLOYMENT_NAME,
            credential=credential,
        )

        async with (
            AzureAIClient(**client_kwargs).create_agent(
                name="TicketClassifier",
                instructions=(
                    "You are a customer-support ticket classifier. "
                    "Read the customer ticket and respond with EXACTLY one category "
                    "(Billing, Technical, or General) followed by a one-sentence reason. "
                    "Format: 'Category: <category>\\nReason: <reason>'"
                ),
            ) as classifier_agent,
            AzureAIClient(**client_kwargs).create_agent(
                name="KnowledgeResearcher",
                instructions=(
                    "You are a knowledge-base researcher for a support team. "
                    "Given the ticket and its classification, provide 2-3 bullet points "
                    "of relevant knowledge-base information that would help draft a reply. "
                    "Be concise and factual."
                ),
            ) as researcher_agent,
            AzureAIClient(**client_kwargs).create_agent(
                name="SupportResponder",
                instructions=(
                    "You are a professional customer-support agent. "
                    "Using the ticket, classification, and knowledge-base notes provided, "
                    "draft a friendly, empathetic, and helpful reply to the customer. "
                    "Keep it under 150 words."
                ),
            ) as responder_agent,
        ):
            classifier = ClassifierExecutor(classifier_agent)
            researcher = ResearcherExecutor(researcher_agent)
            responder = ResponderExecutor(responder_agent)

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
