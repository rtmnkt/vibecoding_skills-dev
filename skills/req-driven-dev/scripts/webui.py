# /// script
# requires-python = ">=3.10"
# dependencies = ["nicegui>=2.0", "duckdb", "pyyaml"]
# ///
"""
req-driven-dev Web UI — NiceGUI dashboard for requirement tracking.

Usage:
    uv run scripts/webui.py [--port PORT]  # from this skill root
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure state_db can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent))

import state_db  # noqa: E402
from nicegui import app, ui  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_COLORS = {
    "approved": "#2d6a4f",
    "pending": "#7b6d00",
    "failed": "#9d0208",
    "regression": "#9d0208",
    "stale": "#e76f51",
    "none": "#555555",
}

BADGE_LABELS = {
    "approved": "✅ Approved",
    "pending": "⏳ Pending",
    "failed": "❌ Failed",
    "regression": "🔄 Regression",
    "stale": "⚠️ Stale",
    "none": "— No verification",
}


def _effective_status(row: dict) -> str:
    if row.get("is_stale"):
        return "stale"
    v = row.get("v_status")
    if v is None:
        return "none"
    if v in ("failed", "regression"):
        return v
    if row.get("a_decision") == "approved":
        return "approved"
    return "pending"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _load_data(req_filter: str | None = None) -> tuple[dict[str, dict[int, dict]], dict]:
    requirements = state_db.list_requirements(req_filter)
    rows = state_db.query_full_status(req_filter)
    grouped = _group_by_file(requirements, rows)
    summary = _compute_summary(grouped)
    return grouped, summary


def _group_by_file(
    requirements: list[dict], rows: list[dict]
) -> dict[str, dict[int, dict]]:
    """Group requirements by file → req_no and attach criteria rows."""
    criteria_by_key: dict[tuple[str, int], list[dict]] = {}
    for row in rows:
        criteria_by_key.setdefault((row["requirement"], row["req_no"]), []).append(row)

    grouped: dict[str, dict[int, dict]] = {}
    for req in requirements:
        f = req["file"]
        n = req["req_no"]
        grouped.setdefault(f, {})[n] = {
            "req_text": req["text"],
            "criteria": criteria_by_key.get((f, n), []),
        }
    return grouped


def _requirement_status(req_data: dict) -> str:
    criteria = req_data["criteria"]
    if not criteria:
        return "none"
    if any(c.get("is_stale") for c in criteria):
        return "stale"
    if any(c.get("v_status") == "regression" for c in criteria):
        return "regression"
    if any(c.get("v_status") == "failed" for c in criteria):
        return "failed"
    if all(_effective_status(c) == "approved" for c in criteria):
        return "approved"
    return "pending"


def _compute_summary(grouped: dict[str, dict[int, dict]]) -> dict[str, int]:
    """Compute dashboard summary counts from visible requirements."""
    summary = {
        "total": 0,
        "approved": 0,
        "pending": 0,
        "failed": 0,
        "regression": 0,
        "stale": 0,
        "no_verification": 0,
    }
    for reqs in grouped.values():
        for req_data in reqs.values():
            summary["total"] += 1
            status = _requirement_status(req_data)
            if status == "approved":
                summary["approved"] += 1
            elif status == "failed":
                summary["failed"] += 1
            elif status == "regression":
                summary["regression"] += 1
            elif status == "stale":
                summary["stale"] += 1
            else:
                summary["pending"] += 1
                if status == "none":
                    summary["no_verification"] += 1
    return summary


def _matches_filter_status(status: str, f: str) -> bool:
    if f == "all":
        return True
    if f == "pending":
        return status in ("pending", "none")
    if f == "problems":
        return status in ("failed", "regression", "stale")
    return status == f


# ---------------------------------------------------------------------------
# UI components
# ---------------------------------------------------------------------------


def build_summary_bar(container, summary: dict) -> None:
    container.clear()
    total = summary["total"]
    with container:
        with ui.row().classes("w-full gap-4 items-center"):
            _stat_card("Total", total, "#1976D2")
            _stat_card("Approved", summary["approved"], STATUS_COLORS["approved"])
            _stat_card("Pending", summary["pending"], STATUS_COLORS["pending"])
            _stat_card("Failed", summary["failed"], STATUS_COLORS["failed"])
            _stat_card("Regression", summary["regression"], STATUS_COLORS["regression"])
            _stat_card("Stale", summary["stale"], STATUS_COLORS["stale"])

        if total > 0:
            pct = summary["approved"] / total * 100
            with ui.row().classes("w-full items-center gap-2"):
                ui.linear_progress(value=pct / 100).classes("flex-grow").props(
                    f"color=positive size=24px"
                )
                ui.label(f"{pct:.0f}%").classes("text-lg font-bold")


def _stat_card(label: str, value: int, color: str) -> None:
    with ui.card().classes("p-3 text-center").style(
        f"min-width:80px; border-left:4px solid {color}"
    ):
        ui.label(str(value)).classes("text-2xl font-bold")
        ui.label(label).classes("text-xs text-gray-400")


def build_requirement_cards(
    container,
    grouped: dict[str, dict[int, dict]],
    status_filter: str,
    refresh_fn,
) -> None:
    container.clear()
    with container:
        for file_name in sorted(grouped.keys()):
            reqs = grouped[file_name]
            with ui.card().classes("w-full mb-4"):
                ui.label(f"📄 {file_name}.md").classes(
                    "text-lg font-bold text-blue-300"
                )
                ui.separator()

                for req_no in sorted(reqs.keys()):
                    req_data = reqs[req_no]
                    req_status = _requirement_status(req_data)
                    if not _matches_filter_status(req_status, status_filter):
                        continue

                    criteria = req_data["criteria"]
                    visible_criteria = (
                        criteria
                        if status_filter == "all"
                        else [c for c in criteria if _matches_filter(c, status_filter)]
                    )
                    req_text = req_data["req_text"]
                    with ui.column().classes("w-full ml-2 mb-3"):
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.label(f"{req_no}. {req_text}").classes(
                                "font-medium text-md flex-grow"
                            )
                            ui.badge(BADGE_LABELS[req_status]).style(
                                f"background:{STATUS_COLORS[req_status]}"
                            )
                            fn = file_name
                            rn = req_no
                            ui.button(
                                "+ Criterion",
                                on_click=lambda f=fn, n=rn: show_add_criteria_dialog(
                                    f, n, refresh_fn
                                ),
                            ).props("dense flat size=xs color=grey"
                            ).classes("text-xs")

                        if not criteria:
                            ui.label("No acceptance criteria defined").classes(
                                "text-sm text-gray-400 ml-4"
                            )
                            continue

                        for crit in visible_criteria:
                            _build_criterion_row(crit, refresh_fn)


def _matches_filter(row: dict, f: str) -> bool:
    return _matches_filter_status(_effective_status(row), f)


def _build_criterion_row(crit: dict, refresh_fn) -> None:
    status = _effective_status(crit)
    color = STATUS_COLORS.get(status, "#555")
    label = BADGE_LABELS.get(status, status)

    with ui.card().classes("w-full ml-4 p-2").style(
        f"border-left:3px solid {color}"
    ):
        with ui.row().classes("w-full items-center gap-2"):
            ui.badge(label).style(f"background:{color}")
            ui.label(
                f"{crit['criteria_id']}: {crit['criterion']}"
            ).classes("flex-grow text-sm")

        # Verification detail (expandable)
        if crit.get("v_status"):
            with ui.expansion("Verification detail").classes(
                "w-full text-xs"
            ):
                ui.label(f"Status: {crit['v_status']}").classes("text-xs")
                if crit.get("v_detail"):
                    ui.label(f"Detail: {crit['v_detail']}").classes(
                        "text-xs text-gray-400"
                    )
                if crit.get("v_limitation"):
                    ui.label(
                        f"Limitation: {crit['v_limitation']}"
                    ).classes("text-xs text-orange-400")
                if crit.get("verified_at"):
                    ui.label(f"At: {crit['verified_at']}").classes(
                        "text-xs text-gray-500"
                    )
                if crit.get("a_decision"):
                    ui.label(
                        f"Decision: {crit['a_decision']}"
                    ).classes("text-xs")
                    if crit.get("a_comment"):
                        ui.label(
                            f"Comment: {crit['a_comment']}"
                        ).classes("text-xs text-gray-400")

        # Stale warning
        if crit.get("is_stale"):
            ui.label(
                "⚠️ STALE: requirement text has changed since verification"
            ).classes("text-xs text-orange-400 mt-1")

        # Approve/Reject buttons for pending verifications
        if crit.get("v_id") and not crit.get("a_decision"):
            vid = crit["v_id"]
            with ui.row().classes("gap-2 mt-1"):
                comment_input = ui.input(
                    placeholder="Comment (optional)"
                ).classes("flex-grow").props("dense")

                async def do_approve(
                    v=vid, inp=comment_input, rf=refresh_fn
                ):
                    try:
                        state_db.approve(v, "approved", inp.value or "")
                        ui.notify("Approved!", type="positive")
                        await rf()
                    except Exception as e:
                        ui.notify(str(e), type="negative")

                async def do_reject(
                    v=vid, inp=comment_input, rf=refresh_fn
                ):
                    try:
                        state_db.approve(v, "rejected", inp.value or "")
                        ui.notify("Rejected", type="warning")
                        await rf()
                    except Exception as e:
                        ui.notify(str(e), type="negative")

                ui.button("✅ Approve", on_click=do_approve).props(
                    "dense color=positive size=sm"
                )
                ui.button("❌ Reject", on_click=do_reject).props(
                    "dense color=negative size=sm"
                )


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


def show_add_requirement_dialog(refresh_fn) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-96"):
        ui.label("Add Requirement").classes("text-lg font-bold")

        files = state_db.list_requirement_files()
        new_file_option = "(new)"
        default_file = files[0] if files else new_file_option
        file_input = ui.select(
            label="Requirement file",
            options=files + [new_file_option],
            value=default_file,
        ).classes("w-full")
        new_file_input = ui.input(
            label="New file name (e.g. 0006)",
        ).classes("w-full")
        new_file_input.set_visibility(default_file == new_file_option)

        def on_file_change(e):
            is_new_file = e.value == new_file_option
            new_file_input.set_visibility(is_new_file)
            if not is_new_file:
                new_file_input.value = ""

        file_input.on("update:model-value", on_file_change)

        text_input = ui.textarea(
            label="Requirement text",
            placeholder="要件テキストを入力",
        ).classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):

            async def do_add():
                selected_file = (file_input.value or "").strip()
                requirement_text = (text_input.value or "").strip()
                effective_file = (
                    (new_file_input.value or "").strip()
                    if selected_file == new_file_option
                    else selected_file
                )

                if not effective_file:
                    ui.notify("Requirement file is required", type="warning")
                    return
                if not requirement_text:
                    ui.notify("Requirement text is required", type="warning")
                    return
                try:
                    entry = state_db.add_requirement(effective_file, requirement_text)
                    ui.notify(
                        f"Added: {entry['id']} ({entry['file']}#{entry['req_no']})",
                        type="positive",
                    )
                    dlg.close()
                    await refresh_fn()
                except Exception as e:
                    ui.notify(str(e), type="negative")

            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Add", on_click=do_add).props("color=primary")

    dlg.open()


def show_add_criteria_dialog(
    req_file: str, req_no: int, refresh_fn
) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-96"):
        ui.label(f"Add Criterion for {req_file}#{req_no}").classes(
            "text-lg font-bold"
        )
        text_input = ui.textarea(
            label="Acceptance criterion",
            placeholder="受入条件を入力",
        ).classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):

            async def do_add():
                if not text_input.value.strip():
                    ui.notify("Text is required", type="warning")
                    return
                try:
                    entry = state_db.add_criteria(
                        req_file, req_no, text_input.value.strip()
                    )
                    ui.notify(
                        f"Added: {entry['id']}",
                        type="positive",
                    )
                    dlg.close()
                    await refresh_fn()
                except Exception as e:
                    ui.notify(str(e), type="negative")

            ui.button("Cancel", on_click=dlg.close).props("flat")
            ui.button("Add", on_click=do_add).props("color=primary")

    dlg.open()


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------


def create_page() -> None:
    # State
    current_filter = {"value": "all"}
    _refreshing = {"value": False}

    async def refresh():
        if _refreshing["value"]:
            return
        _refreshing["value"] = True
        try:
            grouped, summary = _load_data()
            build_summary_bar(summary_container, summary)
            build_requirement_cards(
                cards_container, grouped, current_filter["value"], refresh
            )
        except Exception as e:
            ui.notify(f"Refresh error: {e}", type="negative")
        finally:
            _refreshing["value"] = False

    # Header
    with ui.header().classes("items-center justify-between"):
        ui.label("req-driven-dev Dashboard").classes(
            "text-xl font-bold"
        )
        with ui.row().classes("gap-2"):
            ui.button(
                "➕ Requirement",
                on_click=lambda: show_add_requirement_dialog(refresh),
            ).props("dense")
            ui.button("🔄 Refresh", on_click=refresh).props("dense")

    with ui.tabs().classes("w-full") as tabs:
        dashboard_tab = ui.tab("Dashboard", icon="dashboard")
        deps_tab = ui.tab("Dependencies", icon="account_tree")

    with ui.tab_panels(tabs, value=dashboard_tab).classes(
        "w-full max-w-5xl mx-auto"
    ):
        # ---- Dashboard Tab ----
        with ui.tab_panel(dashboard_tab):
            # Filter buttons
            with ui.row().classes("w-full gap-2 mb-4"):
                for fval, flabel in [
                    ("all", "All"),
                    ("pending", "Pending"),
                    ("problems", "Failed / Regression / Stale"),
                    ("approved", "Approved"),
                ]:

                    def make_filter(v=fval):
                        async def f():
                            current_filter["value"] = v
                            await refresh()

                        return f

                    ui.button(flabel, on_click=make_filter()).props(
                        "dense outline"
                    )

            # Containers INSIDE the tab panel for correct NiceGUI parenting
            summary_container = ui.column().classes("w-full")
            ui.separator()
            cards_container = ui.column().classes("w-full")

        # ---- Dependencies Tab ----
        with ui.tab_panel(deps_tab):

            async def refresh_mermaid():
                mermaid_container.clear()
                with mermaid_container:
                    try:
                        diagram = state_db.generate_mermaid()
                        ui.mermaid(diagram).classes("w-full")
                    except Exception as e:
                        ui.label(f"Error: {e}").classes("text-red-500")

            ui.button(
                "🔄 Refresh", on_click=refresh_mermaid
            ).props("dense")
            mermaid_container = ui.column().classes("w-full mt-4")

    # Auto-refresh
    ui.timer(60.0, refresh)

    # Initial load
    ui.timer(0.1, refresh, once=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="req-driven-dev Web UI Dashboard"
    )
    parser.add_argument(
        "--port", type=int, default=9421, help="Port (default: 9421)"
    )
    args = parser.parse_args()

    ui.dark_mode(True)
    create_page()

    ui.run(
        title="req-driven-dev Dashboard",
        port=args.port,
        reload=False,
        show=False,
    )


if __name__ == "__main__":
    main()
