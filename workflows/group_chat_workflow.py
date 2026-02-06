"""
Group Chat Workflow — Product Launch Brainstorm
================================================
Real-world use case: A cross-functional team brainstorms a product launch
strategy. Three specialist agents participate in a round-robin group chat:

  1. **MarketingLead** — Focuses on messaging, positioning, and campaigns.
  2. **EngineeringLead** — Focuses on feature readiness, technical constraints.
  3. **ProductManager** — Synthesizes inputs, drives decisions, produces the
     final launch plan.

The agents take turns responding to each other for a configurable number of
rounds, simulating a real meeting. A **Moderator** executor orchestrates the
round-robin loop and collects the final plan.
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

DEFAULT_ROUNDS = 3


# ---------------------------------------------------------------------------
# Group Chat Moderator — orchestrates round-robin conversation
# ---------------------------------------------------------------------------
class GroupChatModerator(Executor):
    """
    Manages the group-chat loop.

    Flow:
      1. Receives the initial topic → seeds conversation → sends to first agent.
      2. Each agent produces a response; moderator routes to the next agent.
      3. After N rounds, asks the ProductManager for a final summary and yields output.
    """

    def __init__(
        self,
        agents: dict[str, ChatAgent],
        turn_order: list[str],
        max_rounds: int = DEFAULT_ROUNDS,
        id: str = "moderator",
    ):
        self.agents = agents
        self.turn_order = turn_order
        self.max_rounds = max_rounds
        self._turn_index = 0
        self._round = 0
        self._conversation: list[ChatMessage] = []
        self._events: list[dict] = []
        super().__init__(id=id)

    @handler
    async def handle_topic(self, message: ChatMessage, ctx: WorkflowContext[Any, list[dict]]) -> None:
        """Entry point: receives the discussion topic and runs the chat loop."""
        self._conversation = [message]
        self._events = []

        # Add a system-level framing message
        framing = ChatMessage(
            role=Role.USER,
            text=(
                f"You are in a group brainstorming meeting. The topic is:\n\n"
                f"{message.text}\n\n"
                f"Participants: {', '.join(self.turn_order)}. "
                f"Please contribute your perspective concisely (under 100 words). "
                f"Build on what others have said."
            ),
        )
        self._conversation = [framing]

        # Run the round-robin loop
        for self._round in range(self.max_rounds):
            for agent_name in self.turn_order:
                agent = self.agents[agent_name]
                response = await agent.run(list(self._conversation))
                reply_text = response.text

                # Record the turn
                assistant_msg = ChatMessage(
                    role=Role.ASSISTANT,
                    text=f"[{agent_name}]: {reply_text}",
                )
                self._conversation.append(assistant_msg)

                event_entry = {
                    "type": "group_chat_turn",
                    "agent": agent_name,
                    "round": self._round + 1,
                    "content": reply_text,
                }
                self._events.append(event_entry)

        # Final summary from ProductManager
        summary_prompt = ChatMessage(
            role=Role.USER,
            text=(
                "The brainstorming rounds are complete. As the Product Manager, "
                "please synthesize all the inputs into a concise launch plan with: "
                "1) Key messages, 2) Feature highlights, 3) Timeline, 4) Action items. "
                "Keep it under 200 words."
            ),
        )
        self._conversation.append(summary_prompt)
        pm_agent = self.agents.get("ProductManager") or list(self.agents.values())[-1]
        final_response = await pm_agent.run(list(self._conversation))

        self._events.append({
            "type": "output",
            "agent": "ProductManager",
            "round": self._round + 1,
            "content": final_response.text,
        })

        await ctx.yield_output(self._events)


# ---------------------------------------------------------------------------
# Public API — called from the Streamlit UI
# ---------------------------------------------------------------------------
async def run_group_chat_workflow(
    topic: str,
    max_rounds: int = DEFAULT_ROUNDS,
    on_event=None,
) -> list[dict]:
    """
    Execute the group-chat brainstorm and return structured events.

    Parameters
    ----------
    topic : str
        The product launch topic to brainstorm.
    max_rounds : int
        Number of full rounds (each agent speaks once per round).
    on_event : callable, optional
        Callback invoked per turn: ``(event_dict) -> None``.

    Returns
    -------
    list[dict]
        Events with keys ``type``, ``agent``, ``round``, ``content``.
    """
    all_events: list[dict] = []

    async with DefaultAzureCredential() as credential:
        client_kwargs = dict(
            project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
            model_deployment_name=FOUNDRY_MODEL_DEPLOYMENT_NAME,
            credential=credential,
        )

        async with (
            AzureAIClient(**client_kwargs).create_agent(
                name="MarketingLead",
                instructions=(
                    "You are the Marketing Lead in a product launch brainstorm. "
                    "Focus on brand messaging, target audience, campaign channels, "
                    "and competitive positioning. Be creative but practical. "
                    "Keep responses under 100 words. Reference other participants' points."
                ),
            ) as marketing_agent,
            AzureAIClient(**client_kwargs).create_agent(
                name="EngineeringLead",
                instructions=(
                    "You are the Engineering Lead in a product launch brainstorm. "
                    "Focus on feature readiness, technical milestones, scalability "
                    "concerns, and integration points. Be realistic about timelines. "
                    "Keep responses under 100 words. Build on the discussion."
                ),
            ) as engineering_agent,
            AzureAIClient(**client_kwargs).create_agent(
                name="ProductManager",
                instructions=(
                    "You are the Product Manager leading a product launch brainstorm. "
                    "Synthesize marketing and engineering perspectives. Focus on "
                    "prioritization, go-to-market strategy, success metrics, and risks. "
                    "Keep responses under 100 words. Drive toward actionable decisions."
                ),
            ) as pm_agent,
        ):
            agents = {
                "MarketingLead": marketing_agent,
                "EngineeringLead": engineering_agent,
                "ProductManager": pm_agent,
            }
            turn_order = ["MarketingLead", "EngineeringLead", "ProductManager"]

            moderator = GroupChatModerator(
                agents=agents,
                turn_order=turn_order,
                max_rounds=max_rounds,
            )

            workflow = (
                WorkflowBuilder()
                .set_start_executor(moderator)
                .build()
            )

            user_msg = ChatMessage(role=Role.USER, text=topic)

            async for event in workflow.run_stream(user_msg):
                if isinstance(event, WorkflowOutputEvent):
                    # event.data is our list[dict] of turns
                    for turn in event.data:
                        all_events.append(turn)
                        if on_event:
                            on_event(turn)
                elif isinstance(event, WorkflowStatusEvent):
                    entry = {
                        "type": "status",
                        "agent": "workflow",
                        "round": 0,
                        "content": str(event.state),
                    }
                    all_events.append(entry)
                    if on_event:
                        on_event(entry)

    return all_events


# ---------------------------------------------------------------------------
# Stand-alone CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sample_topic = (
        "We are launching 'AzureBot Pro' — an AI-powered customer service platform "
        "for mid-market SaaS companies. Launch date target: Q2 2026. "
        "Key differentiators: multi-language support, built-in analytics dashboard, "
        "and seamless CRM integrations. Budget: $500K for launch campaign."
    )

    async def _main():
        events = await run_group_chat_workflow(
            sample_topic,
            max_rounds=2,
            on_event=lambda e: print(
                f"  [Round {e['round']}] {e['agent']}: {e['content'][:120]}..."
                if len(e.get("content", "")) > 120
                else f"  [Round {e['round']}] {e['agent']}: {e['content']}"
            ),
        )
        print("\n--- Final Launch Plan ---")
        for e in events:
            if e["type"] == "output":
                print(e["content"])

    asyncio.run(_main())
