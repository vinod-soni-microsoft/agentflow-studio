"""
admin-5794 Workflow Demos — Streamlit UI
=========================================
A tabbed dashboard that demonstrates three Azure AI Foundry workflow patterns:
  • Sequential (Customer Support Triage)
  • Human-in-the-Loop (Expense Approval)
  • Group Chat (Product Launch Brainstorm)
"""

import asyncio
import streamlit as st
from config import validate_config

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="admin-5794 · Workflow Demos",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS for polished UI
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 24px;
        border-radius: 8px 8px 0 0;
        font-weight: 600;
    }
    .agent-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 16px; border-radius: 12px; color: white; margin: 8px 0;
    }
    .event-item {
        padding: 8px 12px; border-left: 3px solid #667eea;
        margin: 4px 0; background: #f8f9fa; border-radius: 0 8px 8px 0;
    }
    .status-badge {
        display: inline-block; padding: 4px 12px; border-radius: 12px;
        font-size: 0.8em; font-weight: 600;
    }
    .badge-running { background: #fff3cd; color: #856404; }
    .badge-done { background: #d4edda; color: #155724; }
    .badge-waiting { background: #cce5ff; color: #004085; }
    div[data-testid="stChatMessage"] { max-width: 100%; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/artificial-intelligence.png", width=64)
    st.title("admin-5794")
    st.caption("Azure AI Foundry Workflow Demos")
    st.divider()

    config_ok = validate_config()
    if config_ok:
        st.success("✅ Foundry connection configured")
    else:
        st.error("⚠️ Update `.env` with your Foundry project endpoint")
        st.code(
            "FOUNDRY_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api\n"
            "FOUNDRY_MODEL_DEPLOYMENT_NAME=gpt-4o",
            language="bash",
        )

    st.divider()
    st.markdown("### Workflow Patterns")
    st.markdown(
        "- **Sequential** — pipeline of agents\n"
        "- **Human-in-the-Loop** — pause for approval\n"
        "- **Group Chat** — multi-agent brainstorm"
    )


# ---------------------------------------------------------------------------
# Helper to run async code from Streamlit
# ---------------------------------------------------------------------------
def run_async(coro):
    """Run an async coroutine from synchronous Streamlit code."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tab 1 — Sequential Workflow
# ---------------------------------------------------------------------------
def render_sequential_tab():
    st.header("🔗 Sequential Workflow — Customer Support Triage")
    st.markdown(
        "A customer support ticket flows through **Classifier → Researcher → Responder** "
        "agents in strict order. Each agent enriches the context before passing it on."
    )

    # Architecture diagram
    with st.expander("📐 Workflow Architecture", expanded=True):
        st.markdown("""
        ```
        ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
        │  📋 Classifier │ ──▶ │  🔍 Researcher    │ ──▶ │  💬 Responder │
        │  (Categorize)  │     │  (KB Lookup)       │     │  (Draft Reply)│
        └──────────────┘     └──────────────────┘     └──────────────┘
        ```
        """)

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Submit a Support Ticket")

        # Initialize default ticket text in the widget key directly
        if "seq_ticket" not in st.session_state:
            st.session_state["seq_ticket"] = (
                "Hi, I was charged twice for my subscription last month. "
                "Order #12345. I've been a customer for 3 years and this is really "
                "frustrating. Please help me get a refund ASAP."
            )

        ticket = st.text_area(
            "Customer ticket text:",
            height=150,
            key="seq_ticket",
        )

        sample_tickets = {
            "Billing Issue": "I was charged twice for order #12345. Please refund the duplicate charge.",
            "Technical Bug": "The dashboard keeps crashing when I try to export reports to PDF. Error code: E-5021.",
            "General Inquiry": "Can you tell me about your enterprise plan pricing and what features are included?",
        }

        def _set_sample_ticket(text):
            st.session_state["seq_ticket"] = text

        st.markdown("**Quick samples:**")
        for label, text in sample_tickets.items():
            st.button(label, key=f"seq_sample_{label}", on_click=_set_sample_ticket, args=(text,))

    with col2:
        st.subheader("Workflow Execution")

        if st.button("▶️ Run Sequential Workflow", type="primary", key="seq_run", disabled=not config_ok):
            from workflows.sequential_workflow import run_sequential_workflow

            with st.status("Running sequential workflow...", expanded=True) as status:
                events_container = st.container()
                all_events = []

                def on_event(evt):
                    all_events.append(evt)

                try:
                    events = run_async(run_sequential_workflow(ticket, on_event=on_event))

                    for evt in events:
                        icon = {"status": "⚙️", "output": "✅"}.get(evt["type"], "📨")
                        agent = evt.get("agent", "")
                        if evt["type"] == "output":
                            events_container.success(f"**Final Customer Reply:**\n\n{evt['content']}")
                        elif "Invoked" in evt["type"]:
                            events_container.info(f"{icon} Agent **{agent}** started processing")
                        elif "Completed" in evt["type"]:
                            events_container.info(f"✅ Agent **{agent}** completed")
                        elif evt["type"] == "status":
                            events_container.caption(f"⚙️ Workflow state: `{evt['content']}`")

                    status.update(label="Workflow completed!", state="complete")
                except Exception as e:
                    status.update(label="Workflow failed", state="error")
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Tab 2 — Human-in-the-Loop Workflow
# ---------------------------------------------------------------------------
def render_hitl_tab():
    st.header("🙋 Human-in-the-Loop — Expense Approval")
    st.markdown(
        "An expense report is analyzed by an AI agent, then the workflow **pauses** "
        "for a human manager to approve, reject, or request more info. The final "
        "processing depends on the human decision."
    )

    with st.expander("📐 Workflow Architecture", expanded=True):
        st.markdown("""
        ```
        ┌──────────────┐     ┌────────────────┐     ┌──────────────┐
        │  📊 Analyst    │ ──▶ │  🙋 Human Gate  │ ──▶ │  💼 Processor │
        │  (Analyze)     │     │  (⏸ PAUSE)      │     │  (Finalize)   │
        └──────────────┘     └───────┬────────┘     └──────────────┘
                                      │
                                 ┌────▼─────┐
                                 │  👤 Human  │
                                 │  Decision  │
                                 └──────────┘
        ```
        """)

    # Initialize session state for HITL
    if "hitl_phase" not in st.session_state:
        st.session_state.hitl_phase = "input"  # input → analyzing → decision → processing → done
    if "hitl_analysis_events" not in st.session_state:
        st.session_state.hitl_analysis_events = []
    if "hitl_final_events" not in st.session_state:
        st.session_state.hitl_final_events = []

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Submit Expense Report")
        expense = st.text_area(
            "Expense report details:",
            value=(
                "Expense Report #EXP-2026-0412\n"
                "Employee: Jane Smith | Department: Engineering\n"
                "Date: 2026-01-28\n"
                "Vendor: TechConf Global\n"
                "Amount: $2,450.00\n"
                "Category: Conference Registration\n"
                "Description: Annual AI/ML conference registration including "
                "workshop access and networking dinner.\n"
                "Justification: Required for team upskilling on latest AI frameworks."
            ),
            height=200,
            key="hitl_expense",
        )

        if st.button("▶️ Submit for Analysis", type="primary", key="hitl_submit",
                      disabled=not config_ok or st.session_state.hitl_phase != "input"):
            st.session_state.hitl_phase = "analyzing"
            st.rerun()

        if st.button("🔄 Reset", key="hitl_reset"):
            st.session_state.hitl_phase = "input"
            st.session_state.hitl_analysis_events = []
            st.session_state.hitl_final_events = []
            st.rerun()

    with col2:
        st.subheader("Workflow Progress")

        # Phase: Analyzing
        if st.session_state.hitl_phase == "analyzing":
            with st.status("🔍 Analyzing expense...", expanded=True) as status:
                try:
                    from workflows.human_in_the_loop_workflow import HumanInTheLoopSession

                    session = HumanInTheLoopSession()
                    events = run_async(session.start(st.session_state.hitl_expense))
                    st.session_state.hitl_analysis_events = events
                    # Store session for later (we'll recreate it since async context is tricky)
                    st.session_state.hitl_phase = "decision"
                    status.update(label="Analysis complete — awaiting your decision", state="complete")
                    st.rerun()
                except Exception as e:
                    status.update(label="Analysis failed", state="error")
                    st.error(f"Error: {e}")
                    st.session_state.hitl_phase = "input"

        # Show analysis results if available
        if st.session_state.hitl_analysis_events:
            st.markdown("#### 📊 AI Analysis")
            for evt in st.session_state.hitl_analysis_events:
                if evt["type"] == "status":
                    st.caption(f"⚙️ {evt['content']}")
                elif evt["content"] and len(evt["content"]) > 20:
                    st.info(evt["content"][:500])

        # Phase: Human Decision
        if st.session_state.hitl_phase == "decision":
            st.markdown("---")
            st.markdown(
                '<span class="status-badge badge-waiting">⏸ WORKFLOW PAUSED — Awaiting Human Decision</span>',
                unsafe_allow_html=True,
            )

            decision_col1, decision_col2, decision_col3 = st.columns(3)
            with decision_col1:
                if st.button("✅ Approve", type="primary", key="hitl_approve", use_container_width=True):
                    st.session_state.hitl_decision = "Approved — expense is within policy and budget allocation."
                    st.session_state.hitl_phase = "processing"
                    st.rerun()
            with decision_col2:
                if st.button("❌ Reject", key="hitl_reject", use_container_width=True):
                    st.session_state.hitl_decision = "Rejected — conference budget for Q1 has been fully allocated."
                    st.session_state.hitl_phase = "processing"
                    st.rerun()
            with decision_col3:
                if st.button("❓ Need More Info", key="hitl_moreinfo", use_container_width=True):
                    st.session_state.hitl_decision = "Need More Info — please provide manager pre-approval email and detailed agenda."
                    st.session_state.hitl_phase = "processing"
                    st.rerun()

            custom_decision = st.text_input("Or enter a custom decision:", key="hitl_custom")
            if custom_decision and st.button("Submit Custom Decision", key="hitl_custom_submit"):
                st.session_state.hitl_decision = custom_decision
                st.session_state.hitl_phase = "processing"
                st.rerun()

        # Phase: Processing human decision
        if st.session_state.hitl_phase == "processing":
            decision = st.session_state.get("hitl_decision", "Approved")
            st.markdown(f"**Manager Decision:** {decision}")

            with st.status("💼 Processing with human decision...", expanded=True) as status:
                try:
                    from workflows.human_in_the_loop_workflow import HumanInTheLoopSession

                    async def _run_hitl_end_to_end(expense_text, human_decision):
                        """Run start + decision in one event loop to avoid concurrent-run error."""
                        session = HumanInTheLoopSession()
                        await session.start(expense_text)
                        return await session.submit_decision(human_decision)

                    events = run_async(_run_hitl_end_to_end(st.session_state.hitl_expense, decision))
                    st.session_state.hitl_final_events = events
                    st.session_state.hitl_phase = "done"
                    status.update(label="Processing complete!", state="complete")
                    st.rerun()
                except Exception as e:
                    status.update(label="Processing failed", state="error")
                    st.error(f"Error: {e}")

        # Phase: Done
        if st.session_state.hitl_phase == "done":
            st.markdown("---")
            st.markdown(
                '<span class="status-badge badge-done">✅ WORKFLOW COMPLETE</span>',
                unsafe_allow_html=True,
            )
            for evt in st.session_state.hitl_final_events:
                if evt["type"] == "output":
                    st.success(f"**Final Processing Result:**\n\n{evt['content']}")


# ---------------------------------------------------------------------------
# Tab 3 — Group Chat Workflow
# ---------------------------------------------------------------------------
def render_group_chat_tab():
    st.header("💬 Group Chat — Product Launch Brainstorm")
    st.markdown(
        "Three specialist agents (**Marketing Lead**, **Engineering Lead**, **Product Manager**) "
        "engage in a round-robin brainstorming session. The PM produces a final launch plan."
    )

    with st.expander("📐 Workflow Architecture", expanded=True):
        st.markdown("""
        ```
                        ┌──────────────────────────────────────┐
                        │        🎯 Moderator (Orchestrator)    │
                        └───────────────┬──────────────────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
            ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
            │  📣 Marketing │  │  ⚙️ Engineering│  │  📋 Product   │
            │     Lead      │  │     Lead      │  │   Manager    │
            └──────────────┘  └──────────────┘  └──────────────┘
                    │                   │                   │
                    └───────────────────┼───────────────────┘
                                        │
                              Round-robin × N
        ```
        """)

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Brainstorm Topic")
        topic = st.text_area(
            "Product launch topic:",
            value=(
                "We are launching 'AzureBot Pro' — an AI-powered customer service "
                "platform for mid-market SaaS companies.\n\n"
                "Launch date: Q2 2026\n"
                "Key differentiators: multi-language support, built-in analytics "
                "dashboard, seamless CRM integrations\n"
                "Budget: $500K for launch campaign\n\n"
                "Please brainstorm the go-to-market strategy."
            ),
            height=200,
            key="gc_topic",
        )

        rounds = st.slider(
            "Number of discussion rounds:", min_value=1, max_value=5, value=2, key="gc_rounds"
        )

        agent_colors = {
            "MarketingLead": "🟣",
            "EngineeringLead": "🔵",
            "ProductManager": "🟢",
        }
        st.markdown("**Participants:**")
        for name, color in agent_colors.items():
            st.markdown(f"  {color} **{name}**")

    with col2:
        st.subheader("Group Chat")

        if st.button("▶️ Start Brainstorm", type="primary", key="gc_run", disabled=not config_ok):
            from workflows.group_chat_workflow import run_group_chat_workflow

            chat_container = st.container()

            with st.status(f"Running {rounds}-round brainstorm...", expanded=True) as status:
                try:
                    events = run_async(run_group_chat_workflow(topic, max_rounds=rounds))

                    # Render as a chat-like interface
                    for evt in events:
                        if evt["type"] == "group_chat_turn":
                            agent = evt["agent"]
                            color = agent_colors.get(agent, "⚪")
                            round_num = evt["round"]
                            with chat_container.chat_message(
                                "assistant",
                                avatar=color,
                            ):
                                st.markdown(f"**{agent}** · Round {round_num}")
                                st.write(evt["content"])

                        elif evt["type"] == "output":
                            chat_container.markdown("---")
                            chat_container.markdown("### 📋 Final Launch Plan")
                            chat_container.success(evt["content"])

                    status.update(label="Brainstorm complete!", state="complete")

                except Exception as e:
                    status.update(label="Brainstorm failed", state="error")
                    st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Main — Tabbed layout
# ---------------------------------------------------------------------------
st.title("🔄 Azure AI Foundry Workflow Demos")
st.caption("Project: admin-5794 · Microsoft Agent Framework powered workflows")

tab1, tab2, tab3 = st.tabs([
    "🔗 Sequential Workflow",
    "🙋 Human-in-the-Loop",
    "💬 Group Chat",
])

with tab1:
    render_sequential_tab()

with tab2:
    render_hitl_tab()

with tab3:
    render_group_chat_tab()

# Footer
st.divider()
st.caption(
    "Built with [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) "
    "and [Streamlit](https://streamlit.io) · admin-5794"
)
