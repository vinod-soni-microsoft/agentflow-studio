# Application Insights Integration Guide

This document explains how Azure Application Insights is integrated into the AgentFlow Studio solution to provide end-to-end observability for all AI agents across the three workflow patterns.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AgentFlow Studio (Streamlit)                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐   ┌──────────────────┐   ┌─────────────────────┐  │
│  │ Sequential  │   │ Human-in-the-Loop│   │    Group Chat       │  │
│  │  Workflow   │   │    Workflow       │   │    Workflow         │  │
│  └──────┬──────┘   └────────┬─────────┘   └──────────┬──────────┘  │
│         │                   │                         │             │
│         └───────────────────┼─────────────────────────┘             │
│                             │                                       │
│                   ┌─────────▼──────────┐                            │
│                   │  streaming.py      │  ← All agent calls go      │
│                   │  (OpenTelemetry    │    through here             │
│                   │   instrumented)    │                             │
│                   └─────────┬──────────┘                            │
│                             │                                       │
│                   ┌─────────▼──────────┐                            │
│                   │  tracing.py        │  ← Configures Azure        │
│                   │  (Azure Monitor    │    Monitor exporter         │
│                   │   OpenTelemetry)   │                             │
│                   └─────────┬──────────┘                            │
│                             │                                       │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ HTTPS (OpenTelemetry Protocol)
                              ▼
               ┌──────────────────────────────┐
               │  Azure Application Insights  │
               │  (Log Analytics Workspace)   │
               └──────────────────────────────┘
                              │
                              ▼
               ┌──────────────────────────────┐
               │  Observability Dashboard     │
               │  (Streamlit Tab 4)           │
               │  KQL queries via SDK         │
               └──────────────────────────────┘
```

---

## How It Works — Step by Step

### Step 1: Initialization at App Startup

When the application starts, `config.py` imports and calls `init_tracing()` from `tracing.py`:

```python
# config.py
from tracing import init_tracing
init_tracing()
```

The `init_tracing()` function reads the `APPLICATIONINSIGHTS_CONNECTION_STRING` from the `.env` file and configures the Azure Monitor OpenTelemetry exporter:

```python
# tracing.py
from azure.monitor.opentelemetry import configure_azure_monitor

def init_tracing():
    connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    configure_azure_monitor(connection_string=connection_string)
```

This sets up the OpenTelemetry SDK globally so all spans created anywhere in the application are automatically exported to Application Insights.

---

### Step 2: Agent Calls Are Automatically Traced

Every agent interaction in all three workflows flows through a single function: `stream_agent_text()` in `workflows/streaming.py`. This function is instrumented with OpenTelemetry spans:

```python
# workflows/streaming.py
from tracing import get_tracer

_tracer = get_tracer("agentflow-studio.agents")

async def stream_agent_text(agent, messages, emit_delta=None, workflow_name="", agent_name=""):
    with _tracer.start_as_current_span(
        span_name,
        attributes={
            "agent.name": agent_name,
            "workflow.name": workflow_name,
            "agent.input_length": len(input_text),
            "agent.input_preview": input_text[:500],
        },
    ) as span:
        # ... stream tokens from the agent ...
        span.set_attribute("agent.output_length", len(full))
        span.set_attribute("agent.output_preview", full[:500])
```

Each agent call creates a **span** that records:
- Which workflow triggered it (sequential, human-in-the-loop, group-chat)
- Which agent executed (classifier, researcher, responder, etc.)
- Input/output text previews (first 500 chars)
- Input/output text lengths
- Duration (automatic)
- Success/failure status

---

### Step 3: Workflow-Level Spans Wrap Agent Spans

Each workflow's public API also creates a parent span that wraps all agent calls within that workflow run:

```python
# Example from sequential_workflow.py
_wf_tracer = get_tracer("agentflow-studio.workflows")
_wf_span = _wf_tracer.start_span("workflow/sequential", attributes={
    "workflow.name": "sequential",
    "workflow.input": ticket_text[:500]
})

# ... all agent calls happen here as child spans ...

trace_workflow_end(_wf_span, "sequential", success=True)
```

This creates a **parent-child hierarchy** in Application Insights:
```
workflow/sequential (45.2s)
  ├── sequential/classifier (12.3s)
  ├── sequential/researcher (19.1s)
  └── sequential/responder (13.8s)
```

---

### Step 4: Azure AI SDK Auto-Instrumentation

In addition to our custom spans, the **Microsoft Agent Framework SDK** automatically emits its own OpenTelemetry spans when `configure_azure_monitor()` is active. These include:

| Span Name | What It Captures |
|-----------|-----------------|
| `create_agent <AgentName>` | Agent creation time, model, agent type |
| `responses <AgentName>` | LLM API call duration, token counts |
| `invoke_agent <AgentName>:<version>` | Full agent invocation with input/output messages |
| `chat <model>` | Raw chat completion call with token usage |

These SDK spans include rich attributes:
- `gen_ai.usage.input_tokens` — tokens consumed in the prompt
- `gen_ai.usage.output_tokens` — tokens generated in the response
- `gen_ai.response.model` — actual model used (e.g., `gpt-4o-2024-08-06`)
- `gen_ai.agent.name` — agent name
- `gen_ai.agent.id` — agent name + version

---

### Step 5: Observability Dashboard Queries Application Insights

The **Observability Dashboard** tab in the Streamlit app uses the `azure-monitor-query` SDK to execute KQL queries directly against the Application Insights resource:

```python
# monitoring_dashboard.py
from azure.monitor.query import LogsQueryClient

client = LogsQueryClient(credential)
response = client.query_resource(
    resource_id=APP_INSIGHTS_RESOURCE_ID,
    query=kql,
    timespan=timedelta(hours=timespan_hours),
)
```

This allows users to run pre-built analytics queries (token consumption, latency, failure rates, etc.) without leaving the application.

---

## Data Flow Summary

```
1. User triggers a workflow in the Streamlit UI
                    │
2. Agent calls flow through stream_agent_text()
   → Creates OpenTelemetry span with custom attributes
                    │
3. Azure AI SDK emits its own spans automatically
   → Token counts, model info, agent operations
                    │
4. Azure Monitor OpenTelemetry exporter batches & sends spans
   → HTTPS to Application Insights ingestion endpoint
                    │
5. Data lands in Application Insights tables:
   → "dependencies" table (spans/traces)
   → "traces" table (log messages)
                    │
6. Observability Dashboard queries the data via SDK
   → Displays results as tables and charts
```

---

## Configuration

### Required Environment Variables

Add these to your `.env` file:

```bash
# Application Insights connection string (required for tracing)
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=<key>;IngestionEndpoint=https://<region>.in.applicationinsights.azure.com/;LiveEndpoint=https://<region>.livediagnostics.monitor.azure.com/;ApplicationId=<app-id>

# Application Insights resource ID (required for Observability Dashboard queries)
APPLICATIONINSIGHTS_RESOURCE_ID=/subscriptions/<sub-id>/resourceGroups/<rg>/providers/microsoft.insights/components/<name>
```

### Where to Find These Values

1. **Connection String**: Azure Portal → Application Insights resource → Overview → "Connection String" (copy button)
2. **Resource ID**: Azure Portal → Application Insights resource → Properties → "Resource ID"

---

## What Gets Captured Per Agent

| Attribute | Description | Example |
|-----------|-------------|---------|
| `workflow.name` | Parent workflow | `sequential`, `human-in-the-loop`, `group-chat` |
| `agent.name` | Agent identifier | `classifier`, `researcher`, `MarketingLead` |
| `agent.input_preview` | First 500 chars of input | `"The dashboard keeps crashing..."` |
| `agent.output_preview` | First 500 chars of output | `"Category: Technical — Performance..."` |
| `agent.input_length` | Total input character count | `85` |
| `agent.output_length` | Total output character count | `144` |
| `gen_ai.usage.input_tokens` | Tokens in prompt (SDK) | `80` |
| `gen_ai.usage.output_tokens` | Tokens in response (SDK) | `24` |
| `gen_ai.response.model` | Model used (SDK) | `gpt-4o-2024-08-06` |
| `duration` | Execution time in ms | `12273` |

---

## Observability Dashboard — Pre-built Queries

The dashboard (4th tab) includes 10 KQL queries organized by category:

### 🪙 Tokens & Cost
1. **Token Consumption per Agent** — Total input/output tokens by agent
2. **Token Consumption per Workflow** — Aggregate tokens by workflow type
3. **Token Efficiency Ratio** — Output-to-input token ratio per agent
4. **Cost Estimation** — Approximate USD cost based on GPT-4o pricing

### ⚡ Performance
5. **Latency per Agent (P50/P90/P99)** — Percentile latency breakdown
6. **End-to-End Workflow Latency** — Total workflow execution time
7. **Slowest Agent Calls (Top 20)** — Identifies performance bottlenecks

### 🛡️ Reliability
8. **Failure Rate per Agent** — Success/failure counts and percentages

### 🔍 Debugging
9. **Agent Call Timeline** — Chronological trace of all agent calls

### 🔄 Workflow-Specific
10. **Group Chat — Tokens per Agent** — Per-participant token breakdown

---

## Viewing Traces in Azure Portal

Beyond the in-app dashboard, you can also explore traces directly in the Azure Portal:

1. **Transaction Search** — Azure Portal → Application Insights → Transaction search → Filter by operation name containing `workflow/` or agent names
2. **End-to-End Transaction Details** — Click any trace to see the full parent-child span hierarchy
3. **Application Map** — Visualizes the topology of workflow → agent dependencies
4. **Logs (KQL editor)** — Run custom queries in the `dependencies` table

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| No traces appearing | Missing connection string | Add `APPLICATIONINSIGHTS_CONNECTION_STRING` to `.env` |
| Traces delayed | Normal ingestion latency | Wait 2-5 minutes after running a workflow |
| Dashboard shows "No results" | Time range too narrow | Expand the time range selector |
| "WorkspaceNotFoundError" | Wrong query method | Ensure code uses `query_resource()` not `query_workspace()` |
| Token data missing | Workflows not executed | Run at least one workflow, then query |

---

## Files Involved

| File | Role |
|------|------|
| `tracing.py` | Initializes Azure Monitor OpenTelemetry, provides tracer factory and helper functions |
| `config.py` | Calls `init_tracing()` at startup |
| `workflows/streaming.py` | Instruments every agent call with OpenTelemetry spans |
| `workflows/sequential_workflow.py` | Adds workflow-level parent span for sequential pipeline |
| `workflows/human_in_the_loop_workflow.py` | Adds workflow-level parent span for HITL flow |
| `workflows/group_chat_workflow.py` | Adds workflow-level parent span for group chat |
| `monitoring_dashboard.py` | Observability Dashboard UI with KQL query execution |
| `.env` | Contains connection string and resource ID |

---

## Dependencies

```
azure-monitor-opentelemetry>=1.6.0    # Configures OpenTelemetry for Azure Monitor
opentelemetry-api>=1.25.0             # OpenTelemetry tracing API
opentelemetry-sdk>=1.25.0             # OpenTelemetry SDK implementation
azure-monitor-query>=1.4.0            # Programmatic KQL query execution
```
