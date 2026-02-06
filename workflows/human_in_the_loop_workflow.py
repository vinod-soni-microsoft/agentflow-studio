"""
Human-in-the-Loop Workflow — Expense Approval
==============================================
Real-world use case: An employee submits an expense report. The system:
  1. **Analyst** agent reviews the expense and produces a recommendation
     (approve / flag for review).
  2. The workflow **pauses** and waits for a human manager to approve,
     reject, or request more information.
  3. **Processor** agent finalizes the expense based on the human decision.

This demonstrates the request_info / response_handler pattern that signals
the UI to collect human input before the workflow can continue.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from agent_framework import (
    ChatAgent,
    ChatMessage,
    Executor,
    RequestInfoEvent,
    Role,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowOutputEvent,
    WorkflowStatusEvent,
    WorkflowRunState,
    handler,
    response_handler,
)
from agent_framework.azure import AzureAIClient
from azure.identity.aio import DefaultAzureCredential

from config import FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL_DEPLOYMENT_NAME


# ---------------------------------------------------------------------------
# Request data class — carried by RequestInfoEvent
# ---------------------------------------------------------------------------
@dataclass
class HumanDecisionRequest:
    """Payload sent to the UI when the workflow needs a human decision."""
    prompt: str
    options: list[str]
    analysis_summary: str


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
    Pauses the workflow by calling request_info().
    The workflow emits a RequestInfoEvent and state becomes
    IDLE_WITH_PENDING_REQUESTS.  The UI detects this and shows
    an approval form.  Once the user decides, the UI calls
    workflow.send_responses() which routes through the
    @response_handler below.
    """

    _pending_messages: list[ChatMessage] | None = None

    def __init__(self, id: str = "human-gate"):
        super().__init__(id=id)

    @handler
    async def receive_analysis(self, messages: list[ChatMessage], ctx: WorkflowContext[list[ChatMessage]]) -> None:
        """Store the analysis and request human input via request_info."""
        self._pending_messages = messages

        # Extract analysis text for the UI
        analysis_text = ""
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "contents") and last_msg.contents:
                part = last_msg.contents[-1]
                analysis_text = part.text if hasattr(part, "text") else str(part)
            elif hasattr(last_msg, "text"):
                analysis_text = last_msg.text or ""

        # request_info pauses the workflow and emits a RequestInfoEvent
        await ctx.request_info(
            HumanDecisionRequest(
                prompt="Please review the expense analysis above and provide your decision.",
                options=["Approved", "Rejected", "Need More Info"],
                analysis_summary=analysis_text[:500],
            ),
            str,  # expected response type
        )

    @response_handler
    async def handle_human_response(
        self,
        original_request: HumanDecisionRequest,
        response: str,
        ctx: WorkflowContext[list[ChatMessage]],
    ) -> None:
        """Called when the human submits a decision via send_responses."""
        messages = self._pending_messages or []
        messages.append(ChatMessage(role=Role.USER, text=f"Manager decision: {response}"))
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

    def __init__(
        self,
        analyst_instructions: str | None = None,
        processor_instructions: str | None = None,
    ):
        self._workflow = None
        self._events_log: list[dict] = []
        self._pending_request_id: str | None = None
        self._analyst_instructions = analyst_instructions or (
            "You are a corporate expense analyst. Review the submitted expense report "
            "and produce a structured analysis with:\n"
            "1. Expense summary (amount, category, vendor)\n"
            "2. Policy compliance check\n"
            "3. Risk flags (if any)\n"
            "4. Recommendation: APPROVE or FLAG FOR REVIEW\n"
            "Be concise and professional."
        )
        self._processor_instructions = processor_instructions or (
            "You are an expense processing agent. Based on the expense analysis "
            "and the manager's decision, produce a final processing summary:\n"
            "- If approved: confirm processing and expected reimbursement timeline.\n"
            "- If rejected: explain the reason and next steps for the employee.\n"
            "- If more info needed: list the specific information required.\n"
            "Keep the tone professional and helpful."
        )

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
            instructions=self._analyst_instructions,
        ).__aenter__()

        self._processor_agent = await AzureAIClient(**client_kwargs).create_agent(
            name="ExpenseProcessor",
            instructions=self._processor_instructions,
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

        # Use non-streaming run() — it returns a WorkflowRunResult when the
        # workflow reaches IDLE or IDLE_WITH_PENDING_REQUESTS.
        result = await self._workflow.run(user_msg, include_status_events=True)

        # Extract the request_id so we can respond later
        for req_evt in result.get_request_info_events():
            self._pending_request_id = req_evt.request_id

        return self._result_to_dicts(result, on_event)

    async def submit_decision(self, decision: str, on_event=None) -> list[dict]:
        """
        Submit the human decision and resume the workflow.
        Returns remaining events until completion.
        """
        if self._workflow is None:
            raise RuntimeError("Workflow not started. Call start() first.")
        if self._pending_request_id is None:
            raise RuntimeError("No pending request. The workflow may not have paused for input.")

        # Non-streaming send_responses completes the remaining workflow.
        result = await self._workflow.send_responses(
            {self._pending_request_id: decision}
        )
        self._pending_request_id = None

        events = self._result_to_dicts(result, on_event)

        # Cleanup
        await self._cleanup()
        return events

    def _result_to_dicts(self, result, on_event=None) -> list[dict]:
        """Convert a WorkflowRunResult into a list of UI-friendly dicts."""
        batch: list[dict] = []
        # Data-plane events (the result itself is a list of WorkflowEvent)
        for event in result:
            entry = self._event_to_dict(event)
            if entry:
                batch.append(entry)
                self._events_log.append(entry)
                if on_event:
                    on_event(entry)
        # Also include status events for completeness
        for event in result.status_timeline():
            entry = self._event_to_dict(event)
            if entry:
                batch.append(entry)
                self._events_log.append(entry)
                if on_event:
                    on_event(entry)
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
        elif isinstance(event, RequestInfoEvent):
            analysis = ""
            if hasattr(event.data, "analysis_summary"):
                analysis = event.data.analysis_summary
            return {
                "type": "request_info",
                "agent": event.source_executor_id,
                "content": analysis,
            }
        else:
            evt_name = event.__class__.__name__
            executor_id = getattr(event, "executor_id", "")
            return {
                "type": evt_name,
                "agent": executor_id,
                "content": str(event),
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
