"""
Monitoring Dashboard — Application Insights KQL Query Runner
=============================================================
Provides a Streamlit-based UI to execute pre-built KQL queries against
Application Insights and visualize agent performance, token usage, and latency.
"""

import os
import pandas as pd
import streamlit as st
from datetime import timedelta

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# The Application Insights resource ID (used by LogsQueryClient)
_APP_INSIGHTS_CONN_STR = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
_APP_INSIGHTS_RESOURCE_ID = os.getenv(
    "APPLICATIONINSIGHTS_RESOURCE_ID",
    "/subscriptions/4aa3a068-9553-4d3b-be35-5f6660a6253b/resourceGroups/rg-admin-5794/providers/microsoft.insights/components/admin-5794-marketing-appinsights-1390"
)


# ---------------------------------------------------------------------------
# Pre-built KQL Queries
# ---------------------------------------------------------------------------
QUERIES = [
    {
        "id": "token_per_agent",
        "title": "🎯 Token Consumption per Agent",
        "description": "Shows total input/output tokens consumed by each agent across all workflow runs.",
        "category": "Tokens & Cost",
        "kql": """dependencies
| where customDimensions has "gen_ai.usage.input_tokens"
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         model = tostring(customDimensions["gen_ai.response.model"]),
         input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
         output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"])
| summarize total_input_tokens = sum(input_tokens),
            total_output_tokens = sum(output_tokens),
            total_tokens = sum(input_tokens + output_tokens),
            call_count = count()
    by agent, model
| order by total_tokens desc""",
    },
    {
        "id": "token_per_workflow",
        "title": "📊 Token Consumption per Workflow",
        "description": "Aggregate token usage grouped by workflow (Sequential, Human-in-the-Loop, Group Chat).",
        "category": "Tokens & Cost",
        "kql": """dependencies
| where customDimensions has "gen_ai.usage.input_tokens"
| extend workflow = tostring(customDimensions["workflow.name"]),
         agent = tostring(customDimensions["gen_ai.agent.name"]),
         input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
         output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"])
| summarize total_input = sum(input_tokens),
            total_output = sum(output_tokens),
            total_tokens = sum(input_tokens + output_tokens),
            calls = count()
    by workflow
| order by total_tokens desc""",
    },
    {
        "id": "latency_per_agent",
        "title": "⏱️ Latency per Agent (P50/P90/P99)",
        "description": "Percentile latency breakdown for each agent — identifies slow performers.",
        "category": "Performance",
        "kql": """dependencies
| where customDimensions has "agent.name" and customDimensions["agent.name"] != ""
| extend workflow = tostring(customDimensions["workflow.name"]),
         agent = tostring(customDimensions["agent.name"])
| summarize p50_ms = percentile(duration, 50),
            p90_ms = percentile(duration, 90),
            p99_ms = percentile(duration, 99),
            avg_ms = avg(duration),
            max_ms = max(duration),
            calls = count()
    by workflow, agent
| order by avg_ms desc""",
    },
    {
        "id": "workflow_e2e_latency",
        "title": "🚀 End-to-End Workflow Latency",
        "description": "Total execution time for each workflow type (average, P95, max).",
        "category": "Performance",
        "kql": """dependencies
| where name startswith "workflow/"
| extend workflow = tostring(customDimensions["workflow.name"])
| summarize avg_duration_sec = round(avg(duration / 1000.0), 2),
            p95_duration_sec = round(percentile(duration / 1000.0, 95), 2),
            max_duration_sec = round(max(duration / 1000.0), 2),
            runs = count()
    by workflow
| order by avg_duration_sec desc""",
    },
    {
        "id": "slowest_calls",
        "title": "🐢 Slowest Agent Calls (Top 20)",
        "description": "Identifies the slowest individual agent invocations — useful for spotting bottlenecks.",
        "category": "Performance",
        "kql": """dependencies
| where customDimensions has "agent.name" and customDimensions["agent.name"] != ""
| extend workflow = tostring(customDimensions["workflow.name"]),
         agent = tostring(customDimensions["agent.name"]),
         input_preview = tostring(customDimensions["agent.input_preview"])
| top 20 by duration desc
| project timestamp, workflow, agent, duration_sec = round(duration / 1000.0, 2), input_preview""",
    },
    {
        "id": "token_efficiency",
        "title": "📈 Token Efficiency Ratio",
        "description": "Output tokens per input token — measures how concise vs verbose each agent is.",
        "category": "Tokens & Cost",
        "kql": """dependencies
| where customDimensions has "gen_ai.usage.input_tokens"
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         input_tokens = todouble(customDimensions["gen_ai.usage.input_tokens"]),
         output_tokens = todouble(customDimensions["gen_ai.usage.output_tokens"])
| where input_tokens > 0
| summarize avg_input = round(avg(input_tokens), 0),
            avg_output = round(avg(output_tokens), 0),
            efficiency_ratio = round(avg(output_tokens / input_tokens), 3)
    by agent
| order by avg_input desc""",
    },
    {
        "id": "failure_rate",
        "title": "🚨 Failure Rate per Agent",
        "description": "Shows success/failure counts and failure percentage for each agent.",
        "category": "Reliability",
        "kql": """dependencies
| where customDimensions has "gen_ai.agent.name"
| extend agent = tostring(customDimensions["gen_ai.agent.name"])
| summarize total = count(),
            failures = countif(success == false),
            failure_rate_pct = round(100.0 * countif(success == false) / count(), 2)
    by agent
| order by failure_rate_pct desc""",
    },
    {
        "id": "call_timeline",
        "title": "📅 Agent Call Timeline",
        "description": "Chronological view of all agent calls — useful for tracing a single workflow run.",
        "category": "Debugging",
        "kql": """dependencies
| where customDimensions has "workflow.name"
| extend workflow = tostring(customDimensions["workflow.name"]),
         agent = tostring(customDimensions["agent.name"]),
         op_name = tostring(customDimensions["gen_ai.operation.name"])
| project timestamp, workflow, name, agent, duration_sec = round(duration / 1000.0, 2), success
| order by timestamp asc""",
    },
    {
        "id": "cost_estimation",
        "title": "💰 Cost Estimation (GPT-4o Pricing)",
        "description": "Approximate cost based on token usage with GPT-4o pricing ($2.50/1M input, $10/1M output).",
        "category": "Tokens & Cost",
        "kql": """let input_price_per_1k = 0.0025;
let output_price_per_1k = 0.01;
dependencies
| where customDimensions has "gen_ai.usage.input_tokens"
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         workflow = tostring(customDimensions["workflow.name"]),
         input_tokens = todouble(customDimensions["gen_ai.usage.input_tokens"]),
         output_tokens = todouble(customDimensions["gen_ai.usage.output_tokens"])
| summarize total_input = sum(input_tokens),
            total_output = sum(output_tokens),
            est_cost_usd = round(sum(input_tokens / 1000.0 * input_price_per_1k + output_tokens / 1000.0 * output_price_per_1k), 4)
    by workflow, agent
| order by est_cost_usd desc""",
    },
    {
        "id": "group_chat_tokens",
        "title": "💬 Group Chat — Tokens per Agent",
        "description": "Token consumption breakdown for each participant in the Group Chat workflow.",
        "category": "Workflow-Specific",
        "kql": """dependencies
| where customDimensions has "gen_ai.usage.input_tokens"
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
         output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"])
| where agent in ("MarketingLead", "EngineeringLead", "ProductManager")
| summarize avg_input = round(avg(input_tokens), 0),
            avg_output = round(avg(output_tokens), 0),
            total_tokens = sum(input_tokens + output_tokens),
            total_calls = count()
    by agent
| order by total_tokens desc""",
    },
]


# ---------------------------------------------------------------------------
# Query Execution
# ---------------------------------------------------------------------------
@st.cache_resource
def _get_logs_client():
    """Create a cached LogsQueryClient."""
    credential = DefaultAzureCredential()
    return LogsQueryClient(credential)


def execute_kql_query(kql: str, timespan_hours: int = 24) -> tuple[pd.DataFrame | None, str]:
    """
    Execute a KQL query against the Application Insights resource.

    Returns (DataFrame, error_message). If successful, error_message is empty.
    """
    if not _APP_INSIGHTS_RESOURCE_ID:
        return None, "APPLICATIONINSIGHTS_RESOURCE_ID not set."

    try:
        client = _get_logs_client()
        response = client.query_resource(
            resource_id=_APP_INSIGHTS_RESOURCE_ID,
            query=kql,
            timespan=timedelta(hours=timespan_hours),
        )

        if response.status == LogsQueryStatus.SUCCESS:
            table = response.tables[0]
            columns = [col.name if hasattr(col, "name") else str(col) for col in table.columns]
            df = pd.DataFrame(data=table.rows, columns=columns)
            return df, ""
        elif response.status == LogsQueryStatus.PARTIAL:
            table = response.partial_data[0]
            columns = [col.name if hasattr(col, "name") else str(col) for col in table.columns]
            df = pd.DataFrame(data=table.rows, columns=columns)
            return df, "⚠️ Partial results returned."
        else:
            return None, f"Query failed: {response.status}"
    except Exception as e:
        return None, f"Error: {str(e)}"


# ---------------------------------------------------------------------------
# Streamlit UI Renderer
# ---------------------------------------------------------------------------
def render_monitoring_tab():
    """Render the monitoring dashboard tab."""

    st.header("📊 Observability Dashboard")
    st.markdown(
        "Execute pre-built KQL queries to analyze agent performance, token consumption, "
        "and reliability across all three workflows. Results come directly from Application Insights."
    )

    # Connection status
    if _APP_INSIGHTS_CONN_STR:
        st.success("✅ Connected to Application Insights")
    else:
        st.error("⚠️ `APPLICATIONINSIGHTS_CONNECTION_STRING` not set in `.env`")
        return

    # Time range selector
    col_time, col_info = st.columns([1, 3])
    with col_time:
        timespan = st.selectbox(
            "⏰ Time range",
            options=[1, 6, 12, 24, 48, 72, 168],
            format_func=lambda x: f"Last {x}h" if x < 24 else f"Last {x // 24}d",
            index=3,
            key="monitor_timespan",
        )
    with col_info:
        st.caption("")
        st.caption(
            "💡 **Tip:** Application Insights has a ~2-5 min ingestion delay. "
            "Run workflows first, wait a few minutes, then query."
        )

    st.markdown("---")

    # Group queries by category
    categories = {}
    for q in QUERIES:
        cat = q["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(q)

    # Render each category as an expandable section
    for cat_name, cat_queries in categories.items():
        cat_icon = {
            "Tokens & Cost": "🪙",
            "Performance": "⚡",
            "Reliability": "🛡️",
            "Debugging": "🔍",
            "Workflow-Specific": "🔄",
        }.get(cat_name, "📋")

        st.subheader(f"{cat_icon} {cat_name}")

        for query in cat_queries:
            _render_query_card(query, timespan)

        st.markdown("")


def _render_query_card(query: dict, timespan: int):
    """Render a single query card with Run button."""
    qid = query["id"]

    with st.container():
        # Card header with title and run button
        col_title, col_btn = st.columns([5, 1])

        with col_title:
            st.markdown(f"**{query['title']}**")
            st.caption(query["description"])

        with col_btn:
            st.markdown("")  # spacing
            run_clicked = st.button(
                "▶️ Run",
                key=f"run_{qid}",
                type="primary",
                use_container_width=True,
            )

        # Show/hide KQL source
        with st.expander("📝 View KQL Query", expanded=False):
            st.code(query["kql"], language="kql")

        # Execute query when button clicked
        if run_clicked:
            with st.spinner("Executing query..."):
                df, error = execute_kql_query(query["kql"], timespan_hours=timespan)

            if error:
                st.warning(error)

            if df is not None and not df.empty:
                # Display results as a styled dataframe
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                )

                # Show summary metrics
                row_count = len(df)
                st.caption(f"📋 {row_count} row{'s' if row_count != 1 else ''} returned")

                # Offer chart for numeric data
                numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
                if numeric_cols and len(df) > 1:
                    # Auto-detect a good chart
                    label_col = next(
                        (c for c in df.columns if c in ("agent", "workflow", "name", "model")),
                        None,
                    )
                    if label_col and numeric_cols:
                        chart_df = df.set_index(label_col)[numeric_cols[:3]]
                        st.bar_chart(chart_df)

            elif df is not None and df.empty:
                st.info("No results found for the selected time range. Try running workflows first or expanding the time range.")

        st.markdown("---")
