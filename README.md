# AgentFlow Studio — Azure AI Foundry Workflow Demos

Interactive Streamlit dashboard showcasing three core workflow patterns built with
the **Microsoft Agent Framework** and **Azure AI Foundry**.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Agent Framework](https://img.shields.io/badge/Agent_Framework-1.0.0b260107-purple)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-red)

---

## Workflow Patterns

| Pattern | Use Case | Agents |
|---------|----------|--------|
| **Sequential** | Customer Support Triage | Classifier → Researcher → Responder |
| **Human-in-the-Loop** | Expense Approval | Analyst → *Human Gate (pause)* → Processor |
| **Group Chat** | Product Launch Brainstorm | Marketing Lead ↔ Engineering Lead ↔ Product Manager |

---

## Quick Start

### Prerequisites

- Python 3.10+
- An [Azure AI Foundry](https://ai.azure.com) project with a deployed model (e.g., `gpt-4o`)
- Azure CLI logged in (`az login`)

### 1. Clone & setup

```bash
cd agentflow-studio-workflow-demos
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

Copy the example and fill in your Foundry details:

```bash
cp .env.example .env
```

Edit `.env`:
```
FOUNDRY_PROJECT_ENDPOINT=https://<your-project>.services.ai.azure.com/api
FOUNDRY_MODEL_DEPLOYMENT_NAME=gpt-4o
```

### 3. Run the dashboard

```bash
streamlit run app.py
```

The UI opens at **http://localhost:8501** with three tabs.

---

## Project Structure

```
agentflow-studio-workflow-demos/
├── app.py                          # Streamlit UI (main entry point)
├── config.py                       # Shared configuration loader
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment template
├── .gitignore
├── README.md
└── workflows/
    ├── __init__.py
    ├── sequential_workflow.py       # Sequential pipeline demo
    ├── human_in_the_loop_workflow.py # HITL with approval gate
    └── group_chat_workflow.py       # Multi-agent group chat
```

---

## Workflow Details

### 1. Sequential Workflow — Customer Support Triage

A customer ticket is processed through a strict pipeline:
1. **Classifier** categorizes the ticket (Billing / Technical / General)
2. **Researcher** retrieves relevant knowledge-base information
3. **Responder** drafts a customer-facing reply

Each agent's full conversation history is passed forward, giving
downstream agents full context.

### 2. Human-in-the-Loop — Expense Approval

An expense report flows through an AI analyst, then the workflow
**pauses** (`IDLE_WITH_PENDING_REQUESTS`) waiting for a human decision:
- ✅ Approve
- ❌ Reject
- ❓ Request more info

The human decision is fed back into the workflow, and a processor
agent finalizes the outcome.

### 3. Group Chat — Product Launch Brainstorm

Three agents participate in a round-robin discussion:
- **Marketing Lead** — messaging, campaigns, positioning
- **Engineering Lead** — feature readiness, timelines
- **Product Manager** — synthesizes and produces the final launch plan

The number of discussion rounds is configurable via the UI slider.

---

## Debugging with AI Toolkit

The project includes VS Code debug configurations for use with
Agent Inspector. Press **F5** to launch in debug mode.

---

## License

Internal use — AgentFlow Studio project.
