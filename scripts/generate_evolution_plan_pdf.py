"""Generate the SHIMS Omni Evolution Plan as a PDF.

Run: .venv/Scripts/python scripts/generate_evolution_plan_pdf.py
Output: generated/SHIMS_Evolution_Plan.pdf
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    ListFlowable,
    ListItem,
    PageBreak,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated" / "SHIMS_Evolution_Plan.pdf"
OUT.parent.mkdir(parents=True, exist_ok=True)


def build_pdf() -> None:
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=24,
        textColor=colors.HexColor("#0B3D91"),
        spaceAfter=20,
        alignment=1,  # center
    )
    h1 = ParagraphStyle(
        "CustomH1",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=colors.HexColor("#0B3D91"),
        spaceAfter=10,
        spaceBefore=16,
    )
    h2 = ParagraphStyle(
        "CustomH2",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor("#1E5AA8"),
        spaceAfter=8,
        spaceBefore=12,
    )
    h3 = ParagraphStyle(
        "CustomH3",
        parent=styles["Heading3"],
        fontSize=12,
        textColor=colors.HexColor("#2D2D2D"),
        spaceAfter=6,
        spaceBefore=10,
    )
    body = ParagraphStyle(
        "CustomBody",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
        spaceAfter=8,
    )
    bullet = ParagraphStyle(
        "CustomBullet",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
        leftIndent=14,
        bulletIndent=6,
    )
    caption = ParagraphStyle(
        "Caption",
        parent=styles["Italic"],
        fontSize=9,
        textColor=colors.grey,
        alignment=1,
    )

    story: list = []

    story.append(Paragraph("SHIMS Omni — Evolution Plan", title_style))
    story.append(Paragraph("A roadmap for the next 90 days of use", caption))
    story.append(Spacer(1, 0.4 * cm))
    story.append(
        Paragraph(
            "This plan was generated after Phase C/D/E implementation. Use it after you have tested SHIMS Omni for a few days. Each phase is prioritized by impact, risk, and the feedback you are most likely to gather from daily use.",
            body,
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    # Metadata table
    meta = [
        ["Generated", "2026-06-09"],
        ["Version", "v16+ Phase C/D/E"],
        ["Primary goal", "Desktop skill-building self-improving AI agent"],
        ["Baseline", "Wave engine v3, planner, scheduler, native multimodal, enterprise bridge"],
    ]
    meta_table = Table(meta, colWidths=[4 * cm, 10 * cm])
    meta_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0DDF0")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(meta_table)
    story.append(Spacer(1, 0.6 * cm))

    # ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Phase 1 — Hot fixes (first 3 days)", h1))
    story.append(
        Paragraph(
            "These are the issues you will feel immediately. Fix them before adding new depth.",
            body,
        )
    )

    story.append(Paragraph("1.1 Latency & model loading", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Pre-load the default Ollama model on startup so the first request does not pay a 60–180 s cold-start tax.", bullet)),
        ListItem(Paragraph("Add a 'fast mode' switch that routes synthesis to Anthropic/OpenAI when Ollama is cold, while keeping tools local.", bullet)),
        ListItem(Paragraph("Surface per-wave timing in the UI so you can see exactly which step is slow.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("1.2 Voice & interruption polish", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("TTS now uses browser voice only; tune the selected voice per language and allow a user voice preference.", bullet)),
        ListItem(Paragraph("Add 'push to talk' as an alternative to wake-word for noisy environments.", bullet)),
        ListItem(Paragraph("Stop button must cancel both stream and TTS reliably across all routes.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("1.3 Sidebar & information density", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Tabbed right panel is now live (Thinking / Plans / Enterprise / Feed). Next: make each tab independently collapsible and remember user preferences.", bullet)),
        ListItem(Paragraph("Move less-critical gauges (memory, network) into a tooltip so the status strip stays minimal.", bullet)),
        ListItem(Paragraph("Add a global command palette (Ctrl+Shift+P) to jump between tabs, sessions, and tools.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Phase 2 — Make the agent actually useful (weeks 1–2)", h1))
    story.append(
        Paragraph(
            "After the hot fixes, the goal is to make SHIMS finish multi-step tasks without babysitting.",
            body,
        )
    )

    story.append(Paragraph("2.1 Plan learning & self-tuning", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Record which tool_hint values succeed and which fail per description pattern.", bullet)),
        ListItem(Paragraph("After a plan succeeds, auto-generate a skill from the step sequence so future similar requests skip planning.", bullet)),
        ListItem(Paragraph("After a plan fails, prompt the user for the correct step and update the planner's fallback heuristics.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("2.2 Persistent context across sessions", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Auto-summarize long conversation threads and store the summary as memory every N turns.", bullet)),
        ListItem(Paragraph("When a user returns after a break, prepend the top relevant memories to the system prompt.", bullet)),
        ListItem(Paragraph("Add named projects: group sessions, files, plans, and memories under a project label.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("2.3 Better tool use in chat", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Native multimodal is live for Anthropic/OpenAI. Extend it to tool result cards (charts, images) so the model can reason over them.", bullet)),
        ListItem(Paragraph("Tool results longer than 4 KB should be auto-summarized before being sent back to the model.", bullet)),
        ListItem(Paragraph("Let the user edit or retry any tool call from the chat history.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("2.4 Mail automation that actually ships", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("OAuth setup wizard for Gmail in the settings panel.", bullet)),
        ListItem(Paragraph("Rule engine: 'if sender contains X and subject contains Y, label Z and notify me'.", bullet)),
        ListItem(Paragraph("Daily morning brief: unread count + priority senders + action items.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Phase 3 — Depth & integration (weeks 3–6)", h1))
    story.append(
        Paragraph(
            "This is where SHIMS becomes better than Hermes: deep desktop integration, reliable long-horizon execution, and chemistry superpowers.",
            body,
        )
    )

    story.append(Paragraph("3.1 Desktop automation beyond the browser", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Add pyautogui / pydirectinput sandbox for click/type/scroll automation when explicitly allowed.", bullet)),
        ListItem(Paragraph("Screen understanding: capture a screenshot and ask SHIMS to click a UI element by description.", bullet)),
        ListItem(Paragraph("Clipboard bridge: read/write clipboard so SHIMS can paste generated text/code into other apps.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("3.2 Scheduler goes from timer to orchestrator", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Calendar integration: read .ics / Google Calendar to know user availability.", bullet)),
        ListItem(Paragraph("Conditional tasks: 'if my GPU is idle, run training; else queue'.", bullet)),
        ListItem(Paragraph("Notification surface: system toast + browser push + optional email when a scheduled task finishes or needs approval.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("3.3 Enterprise bridge: Omni as controller", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Right-panel dashboard is live. Next: make each department tile clickable so Omni can drill into QC, production, warehouse, etc.", bullet)),
        ListItem(Paragraph("Bi-directional sync: when Omni creates a record via enterprise.command, surface it in the Enterprise UI automatically.", bullet)),
        ListItem(Paragraph("Add an 'Omni approved' audit trail for actions taken on behalf of the user.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("3.4 Chemistry & ChemDFM", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("ChemDFM query/train/journal tools are live. Next: auto-feed every validated chemistry fact from Enterprise R&D into the journal.", bullet)),
        ListItem(Paragraph("Visual molecule renderer: given a SMILES or common name, generate an SVG and attach it to chat.", bullet)),
        ListItem(Paragraph("Periodic 'learning report' that shows which chemistry topics are improving and which need more training data.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Phase 4 — Scale & reliability (weeks 7–12)", h1))

    story.append(Paragraph("4.1 Model garden", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Allow per-tool model selection: router = 3B, coder = 7B/14B, chemistry = ChemDFM, vision = VL model.", bullet)),
        ListItem(Paragraph("Auto-download and cache models so the user does not manually pull each one.", bullet)),
        ListItem(Paragraph("Quantization selector: pick speed/quality trade-off per task.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("4.2 Security & sandbox hardening", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Network-less code sandbox option: block all outbound from desktop.interpreter for sensitive work.", bullet)),
        ListItem(Paragraph("Approval policy presets: 'ask always', 'ask for writes', 'ask for external', 'omnipotent'.", bullet)),
        ListItem(Paragraph("Signed action ledger: append-only log of every tool call for compliance.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("4.3 Mobile & Termux parity", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Ensure planner, scheduler, and memory work on the Termux offline runtime.", bullet)),
        ListItem(Paragraph("Sync state between desktop and mobile via a local-only peer protocol (no cloud required).", bullet)),
        ListItem(Paragraph("Voice-first mobile mode: larger buttons, simplified UI, wake-word optimized for phone mics.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(Paragraph("4.4 Skill marketplace (local)", h2))
    story.append(ListFlowable([
        ListItem(Paragraph("Export/import skills as .shims-skill files so users can share workflows without sharing data.", bullet)),
        ListItem(Paragraph("Skill store: a curated index of community skills vetted by sandbox testing.", bullet)),
        ListItem(Paragraph("Auto-suggest skills based on the user's recent tool usage patterns.", bullet)),
    ], bulletType="bullet", leftIndent=14))

    story.append(PageBreak())

    # ─────────────────────────────────────────────────────────────
    story.append(Paragraph("Success metrics", h1))
    story.append(
        Paragraph(
            "After 90 days, these are the numbers that prove SHIMS has evolved from a chatbot into a real desktop agent:",
            body,
        )
    )
    metrics = [
        ["Metric", "Target", "How to measure"],
        ["First-response latency", "< 3 s for cloud, < 10 s for local", "Wave latency eval"],
        ["Plan completion rate", "> 80 % of auto-plans finish without human retry", "Plan DB status counts"],
        ["Tool success rate", "> 90 % of tool calls return ok=True", "Action ledger"],
        ["Memory recall precision", "Top-3 memory hits relevant to query", "Manual spot checks"],
        ["Daily active scheduled tasks", "> 3 recurring tasks running", "Scheduler DB"],
        ["Enterprise bridge uptime", "> 95 % successful command proxy", "Enterprise status pings"],
        ["Voice command success", "> 85 % correctly understood + acted on", "STT + action match logs"],
        ["User edits after agent output", "< 20 % of turns need correction", "Session logs"],
    ]
    metrics_table = Table(metrics, colWidths=[5.5 * cm, 4.5 * cm, 5.5 * cm])
    metrics_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B3D91")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F5F8FC")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0DDF0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(Spacer(1, 0.3 * cm))
    story.append(metrics_table)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Final note", h1))
    story.append(
        Paragraph(
            "The strongest agents are not the ones with the most features; they are the ones that finish tasks while the user thinks about something else. Use this plan to prioritize ruthlessly: if a feature does not improve task-completion rate or reduce user effort, deprioritize it. SHIMS is already deep; the next evolution is about making that depth feel effortless.",
            body,
        )
    )
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("— SHIMS Agent OS, generated 2026-06-09", caption))

    doc.build(story)
    print(f"PDF written to: {OUT}")


if __name__ == "__main__":
    build_pdf()
