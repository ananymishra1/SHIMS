"""Document template manager — CRUD + line/subline editing for all document types."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.config import settings
DATABASE_PATH = settings.database_path
from shared.security import new_id
from .branded_base import DocumentLine, DocumentSection, FormatConfig


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'generic',
    description TEXT,
    format_json TEXT DEFAULT '{}',
    company_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    is_default INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS document_template_lines (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    value TEXT DEFAULT '',
    type TEXT DEFAULT 'text',
    required INTEGER DEFAULT 0,
    spec TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    indent INTEGER DEFAULT 0,
    font_size INTEGER DEFAULT 10,
    bold INTEGER DEFAULT 0,
    color TEXT DEFAULT '#000000',
    width_pct REAL DEFAULT 100.0,
    section_name TEXT DEFAULT 'default',
    page_break_before INTEGER DEFAULT 0,
    bg_color TEXT,
    FOREIGN KEY(template_id) REFERENCES document_templates(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_template_revisions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    lines_json TEXT,
    format_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(template_id) REFERENCES document_templates(id) ON DELETE CASCADE
);
"""


def _init_tables() -> None:
    with _get_db() as conn:
        conn.executescript(_SCHEMA)


_init_tables()


def _row_to_line(row: sqlite3.Row) -> DocumentLine:
    return DocumentLine(
        key=row["key"],
        label=row["label"],
        value=row["value"] or "",
        type=row["type"] or "text",
        required=bool(row["required"]),
        spec=row["spec"] or "",
        unit=row["unit"] or "",
        order=row["sort_order"],
        indent=row["indent"],
        font_size=row["font_size"] or 10,
        bold=bool(row["bold"]),
        color=row["color"] or "#000000",
        width_pct=row["width_pct"] or 100.0,
    )


def _row_to_line_dict(row: sqlite3.Row) -> dict[str, Any]:
    line = _row_to_line(row).__dict__
    line.update({
        "id": row["id"],
        "template_id": row["template_id"],
        "sort_order": row["sort_order"],
        "section_name": row["section_name"] or "default",
        "page_break_before": bool(row["page_break_before"]),
        "bg_color": row["bg_color"] if "bg_color" in row.keys() else None,
    })
    return line


class DocumentTemplateManager:
    """Manage document templates with full line-level editing."""

    @staticmethod
    def create_template(
        name: str,
        kind: str = "generic",
        description: str = "",
        format_config: FormatConfig | None = None,
        company: dict | None = None,
        is_default: bool = False,
    ) -> str:
        template_id = new_id("tmpl")
        fc = format_config or FormatConfig()
        with _get_db() as conn:
            conn.execute(
                """INSERT INTO document_templates
                   (id, name, kind, description, format_json, company_json, is_default)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    template_id, name, kind, description,
                    json.dumps(fc.__dict__, default=str),
                    json.dumps(company or {}, default=str),
                    1 if is_default else 0,
                ),
            )
        return template_id

    @staticmethod
    def get_template(template_id: str) -> dict[str, Any] | None:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT * FROM document_templates WHERE id=?", (template_id,)
            ).fetchone()
            if not row:
                return None
            lines = conn.execute(
                "SELECT * FROM document_template_lines WHERE template_id=? ORDER BY sort_order",
                (template_id,),
            ).fetchall()
            return {
                "id": row["id"],
                "name": row["name"],
                "kind": row["kind"],
                "description": row["description"],
                "format_config": json.loads(row["format_json"] or "{}"),
                "company": json.loads(row["company_json"] or "{}"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "is_default": bool(row["is_default"]),
                "lines": [_row_to_line_dict(l) for l in lines],
            }

    @staticmethod
    def list_templates(kind: str | None = None) -> list[dict]:
        with _get_db() as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM document_templates WHERE kind=? ORDER BY updated_at DESC",
                    (kind,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM document_templates ORDER BY updated_at DESC"
                ).fetchall()
            return [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "description": r["description"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "is_default": bool(r["is_default"]),
                }
                for r in rows
            ]

    @staticmethod
    def update_template(
        template_id: str,
        name: str | None = None,
        description: str | None = None,
        format_config: FormatConfig | None = None,
        company: dict | None = None,
    ) -> bool:
        with _get_db() as conn:
            updates: list[str] = []
            params: list[Any] = []
            if name is not None:
                updates.append("name=?")
                params.append(name)
            if description is not None:
                updates.append("description=?")
                params.append(description)
            if format_config is not None:
                updates.append("format_json=?")
                params.append(json.dumps(format_config.__dict__, default=str))
            if company is not None:
                updates.append("company_json=?")
                params.append(json.dumps(company, default=str))
            if not updates:
                return False
            updates.append("updated_at=CURRENT_TIMESTAMP")
            params.append(template_id)
            conn.execute(
                f"UPDATE document_templates SET {','.join(updates)} WHERE id=?",
                params,
            )
            return conn.total_changes > 0

    @staticmethod
    def delete_template(template_id: str) -> bool:
        with _get_db() as conn:
            conn.execute("DELETE FROM document_templates WHERE id=?", (template_id,))
            return conn.total_changes > 0

    # ── Line management ────────────────────────────────────────────────

    @staticmethod
    def add_line(
        template_id: str,
        line: DocumentLine,
        section_name: str = "default",
        page_break_before: bool = False,
    ) -> str:
        line_id = new_id("line")
        with _get_db() as conn:
            # Auto-assign sort_order at end if not specified
            if line.order == 0:
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(sort_order),0) FROM document_template_lines WHERE template_id=?",
                    (template_id,),
                ).fetchone()[0]
                line.order = max_order + 10

            conn.execute(
                """INSERT INTO document_template_lines
                   (id, template_id, key, label, value, type, required, spec, unit,
                    sort_order, indent, font_size, bold, color, width_pct,
                    section_name, page_break_before)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    line_id, template_id, line.key, line.label, line.value, line.type,
                    1 if line.required else 0, line.spec, line.unit,
                    line.order, line.indent, line.font_size,
                    1 if line.bold else 0, line.color, line.width_pct,
                    section_name, 1 if page_break_before else 0,
                ),
            )
        return line_id

    @staticmethod
    def update_line(line_id: str, **fields: Any) -> bool:
        allowed = {
            "key", "label", "value", "type", "required", "spec", "unit",
            "sort_order", "indent", "font_size", "bold", "color", "width_pct",
            "section_name", "page_break_before",
        }
        with _get_db() as conn:
            updates: list[str] = []
            params: list[Any] = []
            for k, v in fields.items():
                if k not in allowed:
                    continue
                if k in ("required", "bold", "page_break_before"):
                    v = 1 if v else 0
                updates.append(f"{k}=?")
                params.append(v)
            if not updates:
                return False
            params.append(line_id)
            conn.execute(
                f"UPDATE document_template_lines SET {','.join(updates)} WHERE id=?",
                params,
            )
            return conn.total_changes > 0

    @staticmethod
    def delete_line(line_id: str) -> bool:
        with _get_db() as conn:
            conn.execute("DELETE FROM document_template_lines WHERE id=?", (line_id,))
            return conn.total_changes > 0

    @staticmethod
    def move_line(line_id: str, new_sort_order: int) -> bool:
        """Move a line to a new position; renumbers siblings."""
        with _get_db() as conn:
            row = conn.execute(
                "SELECT template_id, sort_order FROM document_template_lines WHERE id=?",
                (line_id,),
            ).fetchone()
            if not row:
                return False
            template_id = row["template_id"]
            old_order = row["sort_order"]
            # Simple swap-gap: move everything between old and new by 1
            if new_sort_order > old_order:
                conn.execute(
                    """UPDATE document_template_lines
                       SET sort_order = sort_order - 1
                       WHERE template_id=? AND sort_order > ? AND sort_order <= ?""",
                    (template_id, old_order, new_sort_order),
                )
            elif new_sort_order < old_order:
                conn.execute(
                    """UPDATE document_template_lines
                       SET sort_order = sort_order + 1
                       WHERE template_id=? AND sort_order >= ? AND sort_order < ?""",
                    (template_id, new_sort_order, old_order),
                )
            conn.execute(
                "UPDATE document_template_lines SET sort_order=? WHERE id=?",
                (new_sort_order, line_id),
            )
            return True

    @staticmethod
    def add_sibling_after(line_id: str, sibling: DocumentLine) -> str | None:
        """Insert a new line immediately after an existing line."""
        with _get_db() as conn:
            row = conn.execute(
                "SELECT template_id, sort_order, section_name FROM document_template_lines WHERE id=?",
                (line_id,),
            ).fetchone()
            if not row:
                return None
            sibling.order = row["sort_order"] + 1
            # Shift everything after to make room
            conn.execute(
                """UPDATE document_template_lines
                   SET sort_order = sort_order + 2
                   WHERE template_id=? AND sort_order >= ?""",
                (row["template_id"], sibling.order),
            )
            return DocumentTemplateManager.add_line(
                row["template_id"], sibling, section_name=row["section_name"]
            )

    @staticmethod
    def add_subline(parent_line_id: str, subline: DocumentLine) -> str | None:
        """Add a subline under a parent line."""
        with _get_db() as conn:
            row = conn.execute(
                "SELECT template_id, sort_order, section_name, indent FROM document_template_lines WHERE id=?",
                (parent_line_id,),
            ).fetchone()
            if not row:
                return None
            subline.indent = row["indent"] + 1
            subline.order = row["sort_order"] + 1
            conn.execute(
                """UPDATE document_template_lines
                   SET sort_order = sort_order + 2
                   WHERE template_id=? AND sort_order >= ?""",
                (row["template_id"], subline.order),
            )
            return DocumentTemplateManager.add_line(
                row["template_id"], subline, section_name=row["section_name"]
            )

    @staticmethod
    def get_sections(template_id: str) -> list[DocumentSection]:
        """Rebuild DocumentSection list from DB lines."""
        with _get_db() as conn:
            rows = conn.execute(
                """SELECT * FROM document_template_lines
                   WHERE template_id=?
                   ORDER BY sort_order""",
                (template_id,),
            ).fetchall()

        # Group by section_name
        sections_dict: dict[str, list[DocumentLine]] = {}
        section_meta: dict[str, dict] = {}
        for r in rows:
            sname = r["section_name"] or "default"
            sections_dict.setdefault(sname, []).append(_row_to_line(r))
            if sname not in section_meta:
                section_meta[sname] = {
                    "order": r["sort_order"],
                    "page_break": bool(r["page_break_before"]),
                    "bg_color": r["bg_color"],
                }

        sections = []
        for sname, lines in sections_dict.items():
            meta = section_meta.get(sname, {})
            sections.append(DocumentSection(
                title=sname if sname != "default" else "",
                lines=lines,
                order=meta.get("order", 0),
                page_break_before=meta.get("page_break", False),
                bg_color=meta.get("bg_color"),
            ))
        sections.sort(key=lambda s: s.order)
        return sections

    @staticmethod
    def snapshot(template_id: str) -> str:
        """Save a revision snapshot of current template state."""
        snapshot_id = new_id("rev")
        tpl = DocumentTemplateManager.get_template(template_id)
        if not tpl:
            raise ValueError(f"Template {template_id} not found")

        with _get_db() as conn:
            rev_num = conn.execute(
                "SELECT COALESCE(MAX(revision_number),0)+1 FROM document_template_revisions WHERE template_id=?",
                (template_id,),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO document_template_revisions
                   (id, template_id, revision_number, lines_json, format_json)
                   VALUES (?,?,?,?,?)""",
                (
                    snapshot_id, template_id, rev_num,
                    json.dumps(tpl["lines"]),
                    json.dumps(tpl["format_config"]),
                ),
            )
        return snapshot_id

    @staticmethod
    def restore(snapshot_id: str) -> bool:
        """Restore a template to a previous revision."""
        with _get_db() as conn:
            row = conn.execute(
                "SELECT * FROM document_template_revisions WHERE id=?", (snapshot_id,)
            ).fetchone()
            if not row:
                return False
            template_id = row["template_id"]
            lines = json.loads(row["lines_json"] or "[]")
            # Delete current lines
            conn.execute("DELETE FROM document_template_lines WHERE template_id=?", (template_id,))
            # Restore lines directly (avoid nested connection lock)
            for ln in lines:
                line_id = new_id("line")
                conn.execute(
                    """INSERT INTO document_template_lines
                       (id, template_id, key, label, value, type, required, spec, unit,
                        sort_order, indent, font_size, bold, color, width_pct,
                        section_name, page_break_before)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        line_id, template_id,
                        ln.get("key", "field_"),
                        ln.get("label", "Field"),
                        ln.get("value", ""),
                        ln.get("type", "text"),
                        1 if ln.get("required", False) else 0,
                        ln.get("spec", ""),
                        ln.get("unit", ""),
                        ln.get("order", ln.get("sort_order", 0)),
                        ln.get("indent", 0),
                        ln.get("font_size", 10),
                        1 if ln.get("bold", False) else 0,
                        ln.get("color", "#000000"),
                        ln.get("width_pct", 100.0),
                        ln.get("section_name", "default"),
                        1 if ln.get("page_break_before", False) else 0,
                    ),
                )
            # Restore format
            conn.execute(
                "UPDATE document_templates SET format_json=? WHERE id=?",
                (row["format_json"], template_id),
            )
            return True

    @staticmethod
    def clone_template(template_id: str, new_name: str) -> str:
        """Clone an existing template with all its lines."""
        tpl = DocumentTemplateManager.get_template(template_id)
        if not tpl:
            raise ValueError(f"Template {template_id} not found")
        new_id_str = DocumentTemplateManager.create_template(
            name=new_name,
            kind=tpl["kind"],
            description=tpl["description"],
            format_config=FormatConfig(**tpl.get("format_config", {})),
            company=tpl.get("company"),
        )
        for ln in tpl.get("lines", []):
            dl = DocumentLine(
                key=ln.get("key", "field_"),
                label=ln.get("label", "Field"),
                value=ln.get("value", ""),
                type=ln.get("type", "text"),
                required=ln.get("required", False),
                spec=ln.get("spec", ""),
                unit=ln.get("unit", ""),
                order=ln.get("order", ln.get("sort_order", 0)),
                indent=ln.get("indent", 0),
                font_size=ln.get("font_size", 10),
                bold=ln.get("bold", False),
                color=ln.get("color", "#000000"),
                width_pct=ln.get("width_pct", 100.0),
            )
            DocumentTemplateManager.add_line(new_id_str, dl, ln.get("section_name", "default"))
        return new_id_str

    @staticmethod
    def seed_defaults() -> None:
        """Seed default templates if none exist."""
        has_coa = bool(DocumentTemplateManager.list_templates("coa"))
        has_biz = bool(DocumentTemplateManager.list_templates("business"))
        has_sop = bool(DocumentTemplateManager.list_templates("sop"))

        # COA template
        if not has_coa:
            coa_id = DocumentTemplateManager.create_template(
                name="Certificate of Analysis (Default)",
                kind="coa",
                description="Default pharmaceutical COA template with full test parameters",
                format_config=FormatConfig(
                    header_font_size=18,
                    body_font_size=10,
                    table_header_bg="#1E40AF",
                    table_line_bg="#EFF6FF",
                    table_border_color="#93C5FD",
                    primary_color="#1E3A5F",
                    accent_color="#2563EB",
                    show_logo=True,
                    show_page_numbers=True,
                    signature_lines=3,
                ),
            )
            coa_fields = [
                ("description", "Description", "text", True),
                ("appearance", "Appearance of Solution", "text", True),
                ("solubility", "Solubility", "text", True),
                ("identification_ir", "Identification (IR)", "text", True),
                ("identification_uv", "Identification (UV)", "text", True),
                ("identification_hplc", "Identification (HPLC)", "text", True),
                ("melting_point", "Melting Point", "text", True),
                ("loss_on_drying", "Loss on Drying", "number", True),
                ("sulphated_ash", "Sulphated Ash", "number", True),
                ("assay", "Assay (% w/w)", "number", True),
                ("ph", "pH (1% solution)", "number", True),
                ("optical_rotation", "Optical Rotation", "number", True),
                ("related_substances", "Related Substances", "text", True),
                ("heavy_metals", "Heavy Metals", "number", True),
                ("residual_solvents", "Residual Solvents", "text", True),
                ("microbial_limit", "Microbial Limit Test", "text", True),
                ("bulk_density", "Bulk Density", "number", False),
                ("particle_size", "Particle Size Distribution", "text", False),
                ("water_content", "Water Content (Karl Fischer)", "number", True),
                ("chromatographic_purity", "Chromatographic Purity", "number", True),
            ]
            for i, (key, label, typ, req) in enumerate(coa_fields):
                DocumentTemplateManager.add_line(
                    coa_id,
                    DocumentLine(key=key, label=label, type=typ, required=req, order=i * 10),
                    section_name="Test Results",
                )

        # Business document template
        if not has_biz:
            biz_id = DocumentTemplateManager.create_template(
                name="Business Letter (Default)",
                kind="business",
                description="Default business letter with company branding",
            )
            biz_lines = [
                ("date", "Date", "date"),
                ("recipient", "To", "text"),
                ("subject", "Subject", "text"),
                ("body", "Body", "text"),
                ("closing", "Closing", "text"),
            ]
            for i, (key, label, typ) in enumerate(biz_lines):
                DocumentTemplateManager.add_line(
                    biz_id,
                    DocumentLine(key=key, label=label, type=typ, order=i * 10),
                    section_name="Letter Content",
                )

        # GMP SOP template
        if not has_sop:
            sop_sections = [
                ("Purpose", "To define a controlled, GMP-aligned method for the described process so execution is consistent, traceable, reviewable, and compliant."),
                ("Scope", "This SOP applies to all activities described in the process, performed under the oversight of the responsible department."),
                ("Responsibilities", "Operating personnel are responsible for correct execution and real-time documentation. Department head ensures resource readiness and first-level review. QA governs controlled documents, deviation assessment, and final quality oversight."),
                ("Definitions", "SOP: Standard Operating Procedure. GMP: Good Manufacturing Practice. QA: Quality Assurance. IPC: In-process control."),
                ("Safety and PPE", "Use approved PPE defined by area safety assessment and material safety data. Verify ventilation, containment, spill control, and waste segregation before starting."),
                ("Materials and Equipment", "Use only released materials, calibrated instruments, qualified equipment, approved labels, and current controlled forms. Record equipment ID and material batch details where applicable."),
                ("Procedure", "1. Verify latest effective SOP, batch/process instruction, and required forms.\n2. Confirm training status, area/equipment readiness, material status, and safety controls.\n3. Execute operation exactly per approved instruction and record observations contemporaneously.\n4. Escalate deviations or unexpected results to QA before continuing when product quality may be affected.\n5. Review records for completeness, legibility, correction control, attachments, and signatures before closure."),
                ("In-Process Controls", "Monitor critical parameters defined for the process, including time, temperature, pH, quantity, identity, cleanliness, yield, impurity trend, and documentation completeness."),
                ("Acceptance Criteria", "The operation is acceptable only when all predefined limits, material status checks, equipment status checks, documentation checks, and QA hold points are satisfied."),
                ("Deviations and Change Control", "Any unplanned event, missed step, unexplained result, equipment status mismatch, documentation error, or proposed process change must be recorded and assessed through the approved QMS workflow before final disposition."),
                ("Records and Attachments", "Maintain completed forms, equipment logs, cleaning records, material labels, analytical records, printouts, attachments, and review evidence as controlled quality records per the retention schedule."),
                ("Training Impact", "This SOP requires documented training for all affected roles before effective use. Re-training is required after major revision, recurring deviation, or role/process change."),
                ("References", "Current approved master batch record, applicable pharmacopeial specification, site safety procedure, data integrity policy, and approved change control procedure."),
                ("Revision History", "Initial draft generated in SHIMS Document Studio. QA review must confirm site-specific terminology, responsibilities, acceptance criteria, attachments, and training matrix before approval."),
            ]
            sop_id = DocumentTemplateManager.create_template(
                name="GMP SOP (Default)",
                kind="sop",
                description="Default GMP-aligned SOP template with standard pharmaceutical sections",
                format_config=FormatConfig(
                    header_font_size=16,
                    body_font_size=10,
                    watermark_text="DRAFT",
                    footer_text="Controlled document generated by SHIMS Enterprise Document Studio",
                ),
            )
            for i, (section, body) in enumerate(sop_sections):
                DocumentTemplateManager.add_line(
                    sop_id,
                    DocumentLine(
                        key=f"sop_section_{i+1}",
                        label="Controlled Text",
                        value=body,
                        type="text",
                        required=True,
                        order=(i + 1) * 10,
                    ),
                    section_name=section,
                )
