"""
Reusable dynamic execution view for multi-agent workflows.

Instead of a single static "Running workflow..." message, this component renders
a live **execution plan** — an ordered list of steps (agents / checkpoints) whose
status updates in real time (pending → running → done / failed) as workflow
events arrive.

It is UI-framework code for Streamlit and is intentionally decoupled from the
workflow logic: workflows emit plain event dicts, and the containing tab maps
those events onto :class:`ExecutionView` step updates.
"""

from __future__ import annotations

import time
from html import escape
from typing import Any

import streamlit as st


# Ordered status metadata: (emoji, css-badge-class, label)
_STATUS_META: dict[str, tuple[str, str, str]] = {
    "pending": ("⚪", "badge-waiting", "Pending"),
    "running": ("🔄", "badge-running", "Running"),
    "done": ("✅", "badge-done", "Done"),
    "error": ("❌", "badge-error", "Failed"),
    "skipped": ("⏭️", "badge-waiting", "Skipped"),
}

_TERMINAL = {"done", "error", "skipped"}


class ExecutionView:
    """
    A live, self-rendering execution plan.

    Parameters
    ----------
    placeholder : st.delta_generator.DeltaGenerator
        A ``st.empty()`` placeholder the view fully owns and re-renders into.
    title : str
        Heading shown above the plan (e.g. "Execution Plan").
    steps : list[dict]
        Ordered step definitions. Each dict supports:
        ``key`` (required, unique id), ``title`` (required),
        ``icon`` (optional emoji), ``desc`` (optional one-liner).
    subtitle : str, optional
        Small caption under the title.
    """

    def __init__(
        self,
        placeholder,
        title: str,
        steps: list[dict[str, Any]],
        subtitle: str | None = None,
    ) -> None:
        self.placeholder = placeholder
        self.title = title
        self.subtitle = subtitle
        self._steps: list[dict[str, Any]] = []
        self._state: dict[str, dict[str, Any]] = {}
        for step in steps:
            self._add_step_record(step)
        self.render()

    # ------------------------------------------------------------------ #
    # Mutation API
    # ------------------------------------------------------------------ #
    def _add_step_record(self, step: dict[str, Any]) -> None:
        key = step["key"]
        self._steps.append(step)
        self._state[key] = {
            "status": "pending",
            "detail": None,
            "t_start": None,
            "elapsed": None,
        }

    def add_step(self, step: dict[str, Any], render: bool = True) -> None:
        """Append a new step at runtime (e.g. a new group-chat round)."""
        if step["key"] not in self._state:
            self._add_step_record(step)
            if render:
                self.render()

    def set_status(
        self,
        key: str,
        status: str,
        detail: str | None = None,
        render: bool = True,
    ) -> None:
        """Update a step's status and optionally its detail text."""
        if key not in self._state:
            return
        rec = self._state[key]
        if status == "running" and rec["t_start"] is None:
            rec["t_start"] = time.time()
        if status in _TERMINAL and rec["t_start"] is not None and rec["elapsed"] is None:
            rec["elapsed"] = time.time() - rec["t_start"]
        rec["status"] = status
        if detail is not None:
            rec["detail"] = detail
        if render:
            self.render()

    # Convenience helpers -------------------------------------------------
    def start(self, key: str, detail: str | None = None) -> None:
        self.set_status(key, "running", detail)

    def complete(self, key: str, detail: str | None = None) -> None:
        self.set_status(key, "done", detail)

    def fail(self, key: str, detail: str | None = None) -> None:
        self.set_status(key, "error", detail)

    def skip(self, key: str, detail: str | None = None) -> None:
        self.set_status(key, "skipped", detail)

    def fail_active(self, detail: str | None = None) -> None:
        """Mark any running/pending steps as failed (used in except blocks)."""
        for step in self._steps:
            if self._state[step["key"]]["status"] in ("running", "pending"):
                self.set_status(step["key"], "error", detail, render=False)
        self.render()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def _progress(self) -> float:
        total = len(self._steps)
        if not total:
            return 0.0
        done = sum(
            1 for s in self._steps if self._state[s["key"]]["status"] in _TERMINAL
        )
        return done / total

    def _step_html(self, step: dict[str, Any]) -> str:
        rec = self._state[step["key"]]
        emoji, badge_cls, label = _STATUS_META.get(rec["status"], _STATUS_META["pending"])
        icon = step.get("icon", "")
        title = escape(str(step.get("title", step["key"])))
        desc = step.get("desc")
        running = rec["status"] == "running"

        elapsed_html = ""
        if rec["elapsed"] is not None:
            elapsed_html = f'<span class="exec-elapsed">{rec["elapsed"]:.1f}s</span>'

        desc_html = f'<div class="exec-desc">{escape(str(desc))}</div>' if desc else ""

        detail_html = ""
        if rec["detail"]:
            detail_html = f'<div class="exec-detail">{escape(str(rec["detail"]))}</div>'

        pulse = " exec-running" if running else ""
        return (
            f'<div class="exec-step{pulse}">'
            f'  <div class="exec-step-emoji">{emoji}</div>'
            f'  <div class="exec-step-body">'
            f'    <div class="exec-step-head">'
            f'      <span class="exec-step-title">{icon} {title}</span>'
            f'      <span class="status-badge {badge_cls}">{label}</span>'
            f"      {elapsed_html}"
            f"    </div>"
            f"    {desc_html}"
            f"    {detail_html}"
            f"  </div>"
            f"</div>"
        )

    def render(self) -> None:
        steps_html = "".join(self._step_html(s) for s in self._steps)
        header = f'<div class="exec-title">{escape(self.title)}</div>'
        sub = (
            f'<div class="exec-subtitle">{escape(self.subtitle)}</div>'
            if self.subtitle
            else ""
        )
        with self.placeholder.container():
            st.markdown(
                f'<div class="exec-plan">{header}{sub}'
                f'<div class="exec-steps">{steps_html}</div></div>',
                unsafe_allow_html=True,
            )
            st.progress(self._progress())
