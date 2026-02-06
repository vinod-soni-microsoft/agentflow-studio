"""
Human-in-the-Loop Workflow — Expense Approval
==============================================
Real-world use case: An employee submits an expense report. The system:
  1. **Analyst** agent reviews the expense and produces a recommendation
     (approve / flag for review).
  2. The workflow **pauses** and waits for a human manager to approve,
     reject, or request more information.
  3. **Processor** agent finalizes the expense based on the human decision.

This demonstrates the IDLE_WITH_PENDING_REQUESTS state that signals
the UI to collect human input before the workflow can continue.
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
# Executor 1 — Expense Analyst
# ---------------------------------------------------------------------------
class AnalystExecutor(Executor):
    """Analyses the expense and recommends an action."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, id: str = "analyst"):
        self.agent = agent
        super().__init__(id=id)

    @handler
    async def handle(self, message: ChatMessage, ctx: WorkflowContext[list[ChatMessage]]) -> None:
        messages = [message]
        response = await self.agent.run(messages)
        messages.extend(response.messages)
        # Send the analysis downstream (to the human gate)
        await ctx.send_message(messages)


# ---------------------------------------------------------------------------
# Executor 2 — Human Gate (pauses workflow for human decision)
# ---------------------------------------------------------------------------
class HumanGateExecutor(Executor):
    """
    Pauses the workflow by requesting external input.
    The Streamlit UI detects the IDLE_WITH_PENDING_REQUESTS state
    and presents an approval form to the user.
    """

    _pending_messages: list[ChatMessage] | None = None

    def __init__(self, id: str = "human-gate"):
        super().__init__(id=id)

    @handler
    async def receive_analysis(self, messages: list[ChatMessage], ctx: WorkflowContext[list[ChatMessage]]) -> None:
        """Store the analysis and request human input."""
        self._pending_messages = messages
        # Request external input — this causes the workflow state to become
        # IDLE_WITH_PENDING_REQUESTS, signalling the UI to gather human input.
        await ctx.request_external_input(
            {
                "prompt": "Please review the expense analysis above and provide your decision.",
                "options": ["Approved", "Rejected", "Need More Info"],
                "analysis_summary": messages[-1].contents[-1].text if messages else "",
            }
        )

    @handler
    async def receive_human_decision(self, decision: str, ctx: WorkflowContext[list[ChatMessage]]) -> None:
        """Resume after the human provides a decision."""
        messages = self._pending_messages or []
        messages.append(ChatMessage(role=Role.USER, text=f"Manager decision: {decision}"))
        await ctx.send_message(messages)


# ---------------------------------------------------------------------------
# Executor 3 — Expense Processor
# ---------------------------------------------------------------------------
class ProcessorExecutor(Executor):
    """Finalizes the expense based on the human decision."""

    agent: ChatAgent

    def __init__(self, agent: ChatAgent, id: str = "processor"):
        self.agent = agent
        super().__init__(id=id)

    @handler
    async def handle(self, messages: list[ChatMessage], ctx: WorkflowContext[Any, str]) -> None:
        response = await self.agent.run(messages)
        await ctx.yield_output(response.text)


# ---------------------------------------------------------------------------
# Public API — called from the Streamlit UI
# ---------------------------------------------------------------------------
class HumanInTheLoopSession:
    """
    Manages a single expense-approval workflow run.

    Usage from the UI:
        session = HumanInTheLoopSession()
        events  = await session.start(expense_text)
        # ... render events, show approval form ...
        events  = await session.submit_decision("Approved")
    """

    def __init__(self):
        self._workflow = None
        self._stream = None
        self._events_log: list[dict] = []

    async def start(self, expense_text: str, on_event=None) -> list[dict]:
        """
        Start the workflow. Returns events up to the point where
        human input is requested.
        """
        self._credential = DefaultAzureCredential()
        credential = self._credential

        client_kwargs = dict(
            project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
            model_deployment_name=FOUNDRY_MODEL_DEPLOYMENT_NAME,
            credential=credential,
        )

        self._analyst_agent = await AzureAIClient(**client_kwargs).create_agent(
            name="ExpenseAnalyst",
            instructions=(
                "You are a corporate expense analyst. Review the submitted expense report "
                "and produce a structured analysis with:\n"
                "1. Expense summary (amount, category, vendor)\n"
                "2. Policy compliance check\n"
                "3. Risk flags (if any)\n"
                "4. Recommendation: APPROVE or FLAG FOR REVIEW\n"
                "Be concise and professional."
            ),
        ).__aenter__()

        self._processor_agent = await AzureAIClient(**client_kwargs).create_agent(
            name="ExpenseProcessor",
            instructions=(
                "You are an expense processing agent. Based on the expense analysis "
                "and the manager's decision, produce a final processing summary:\n"
                "- If approved: confirm processing and expected reimbursement timeline.\n"
                "- If rejected: explain the reason and next steps for the employee.\n"
                "- If more info needed: list the specific information required.\n"
                "Keep the tone professional and helpful."
            ),
        ).__aenter__()

        analyst = AnalystExecutor(self._analyst_agent)
        self._human_gate = HumanGateExecutor()
        processor = ProcessorExecutor(self._processor_agent)

        self._workflow = (
            WorkflowBuilder()
            .add_edge(analyst, self._human_gate)
            .add_edge(self._human_gate, processor)
            .set_start_executor(analyst)
            .build()
        )

        user_msg = ChatMessage(role=Role.USER, text=expense_text)
        self._stream = self._workflow.run_stream(user_msg)

        return await self._consume_until_pause(on_event)

    async def submit_decision(self, decision: str, on_event=None) -> list[dict]:
        """
        Submit the human decision and resume the workflow.
        Returns remaining events until completion.
        """
        if self._workflow is None:
            raise RuntimeError("Workflow not started. Call start() first.")

        # Send the human decision back into the workflow
        await self._workflow.send_external_input(self._human_gate.id, decision)

        events = await self._consume_until_pause(on_event)

        # Cleanup
        await self._cleanup()
        return events

    async def _consume_until_pause(self, on_event=None) -> list[dict]:
        """Read events from the stream until the workflow pauses or completes."""
        batch: list[dict] = []
        async for event in self._stream:
            entry = self._event_to_dict(event)
            if entry:
                batch.append(entry)
                self._events_log.append(entry)
                if on_event:
                    on_event(entry)

            # Stop reading when human input is needed or workflow is idle
            if isinstance(event, WorkflowStatusEvent):
                if event.state in (
                    WorkflowRunState.IDLE_WITH_PENDING_REQUESTS,
                    WorkflowRunState.IDLE,
                ):
                    break

        return batch

    async def _cleanup(self):
        """Release resources."""
        try:
            if hasattr(self, "_analyst_agent"):
                await self._analyst_agent.__aexit__(None, None, None)
            if hasattr(self, "_processor_agent"):
                await self._processor_agent.__aexit__(None, None, None)
            if hasattr(self, "_credential"):
                await self._credential.close()
        except Exception:
            pass

    @staticmethod
    def _event_to_dict(event) -> dict:
        if isinstance(event, WorkflowStatusEvent):
            return {
                "type": "status",
                "agent": "workflow",
                "content": str(event.state),
            }
        elif isinstance(event, WorkflowOutputEvent):
            return {
                "type": "output",
                "agent": "processor",
                "content": event.data,
            }
        else:
            evt_name = event.__class__.__name__
            executor_id = getattr(event, "executor_id", "")
            # Extract request payload if present
            extra = ""
            if hasattr(event, "data") and isinstance(event.data, dict):
                extra = event.data.get("analysis_summary", "")
            return {
                "type": evt_name,
                "agent": executor_id,
                "content": extra or str(event),
            }

    @property
    def all_events(self) -> list[dict]:
        return list(self._events_log)


# ---------------------------------------------------------------------------
# Stand-alone CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sample_expense = (
        "Expense Report #EXP-2026-0412\n"
        "Employee: Jane Smith\n"
        "Department: Engineering\n"
        "Date: 2026-01-28\n"
        "Vendor: TechConf Global\n"
        "Amount: $2,450.00\n"
        "Category: Conference Registration\n"
        "Description: Annual AI/ML conference registration fee including "
        "workshop access and networking dinner."
    )

    async def _main():
        session = HumanInTheLoopSession()

        print("=== Starting expense analysis ===")
        events = await session.start(
            sample_expense,
            on_event=lambda e: print(f"  [{e['type']}] {e['agent']}: {e['content'][:120]}")
        )

        print("\n=== Human decision: Approved ===")
        events = await session.submit_decision(
            "Approved — conference is pre-approved in Q1 budget.",
            on_event=lambda e: print(f"  [{e['type']}] {e['agent']}: {e['content'][:120]}")
        )

        print("\n--- Final output ---")
        for e in session.all_events:
            if e["type"] == "output":
                print(e["content"])

    asyncio.run(_main())
