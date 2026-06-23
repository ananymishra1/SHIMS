"""Preview engine — generates DRAFT watermarked PDFs for review before finalize."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .branded_base import BrandedPDF, DocumentSection, FormatConfig
from .template_manager import DocumentTemplateManager


class PreviewEngine:
    """Generate preview (draft) versions of any document type.

    Usage:
        path = PreviewEngine.preview_document(template_id, form_data)
        # User reviews, then:
        final_path = PreviewEngine.finalize_document(template_id, form_data)
    """

    @staticmethod
    def _apply_draft_config(config: FormatConfig) -> FormatConfig:
        """Return a copy with DRAFT watermark settings."""
        import copy
        cfg = copy.copy(config)
        cfg.watermark_text = "DRAFT — PREVIEW ONLY"
        cfg.watermark_color = "#FECACA"  # Light red
        cfg.footer_text = "THIS IS A PREVIEW — NOT FOR DISTRIBUTION"
        return cfg

    @staticmethod
    def preview_document(
        template_id: str,
        form_data: dict[str, Any],
        output_path: Path | str | None = None,
    ) -> Path:
        """Generate a preview PDF from a template + form data."""
        tpl = DocumentTemplateManager.get_template(template_id)
        if not tpl:
            raise ValueError(f"Template {template_id} not found")

        config = FormatConfig(**tpl.get("format_config", {}))
        config = PreviewEngine._apply_draft_config(config)
        company = tpl.get("company") or {}

        pdf = BrandedPDF(
            title=f"{tpl['name']} (PREVIEW)",
            doc_id=f"PREVIEW-{tpl['id'][:8]}",
            kind="preview",
            format_config=config,
            company=company,
        )

        # Add metadata
        for k, v in form_data.items():
            if k not in ("template_id", "action"):
                pdf.add_meta(k.replace("_", " ").title(), v)

        # Build sections from template lines
        sections = DocumentTemplateManager.get_sections(template_id)
        for section in sections:
            # Fill in form values
            filled_lines = []
            for line in section.lines:
                val = form_data.get(line.key, line.value)
                filled_lines.append(line.__class__(
                    key=line.key,
                    label=line.label,
                    value=val,
                    type=line.type,
                    required=line.required,
                    spec=line.spec,
                    unit=line.unit,
                    order=line.order,
                    indent=line.indent,
                    font_size=line.font_size,
                    bold=line.bold,
                    color=line.color,
                    width_pct=line.width_pct,
                ))
            pdf.add_section(DocumentSection(
                title=section.title,
                lines=filled_lines,
                order=section.order,
                page_break_before=section.page_break_before,
                bg_color=section.bg_color,
            ))

        if output_path is None:
            from shared.config import GENERATED_DIR
            output_path = GENERATED_DIR / f"preview_{template_id[:8]}_{pdf.doc_id}.pdf"

        return pdf.build(output_path)

    @staticmethod
    def finalize_document(
        template_id: str,
        form_data: dict[str, Any],
        output_path: Path | str | None = None,
    ) -> Path:
        """Generate the final PDF (no DRAFT watermark)."""
        tpl = DocumentTemplateManager.get_template(template_id)
        if not tpl:
            raise ValueError(f"Template {template_id} not found")

        config = FormatConfig(**tpl.get("format_config", {}))
        config.watermark_text = ""
        config.footer_text = tpl.get("format_config", {}).get("footer_text", "")
        company = tpl.get("company") or {}

        pdf = BrandedPDF(
            title=tpl["name"],
            doc_id=f"FINAL-{tpl['id'][:8]}",
            kind=tpl["kind"],
            format_config=config,
            company=company,
        )

        for k, v in form_data.items():
            if k not in ("template_id", "action"):
                pdf.add_meta(k.replace("_", " ").title(), v)

        sections = DocumentTemplateManager.get_sections(template_id)
        for section in sections:
            filled_lines = []
            for line in section.lines:
                val = form_data.get(line.key, line.value)
                filled_lines.append(line.__class__(
                    key=line.key,
                    label=line.label,
                    value=val,
                    type=line.type,
                    required=line.required,
                    spec=line.spec,
                    unit=line.unit,
                    order=line.order,
                    indent=line.indent,
                    font_size=line.font_size,
                    bold=line.bold,
                    color=line.color,
                    width_pct=line.width_pct,
                ))
            pdf.add_section(DocumentSection(
                title=section.title,
                lines=filled_lines,
                order=section.order,
                page_break_before=section.page_break_before,
                bg_color=section.bg_color,
            ))

        if output_path is None:
            from shared.config import GENERATED_DIR
            output_path = GENERATED_DIR / f"final_{template_id[:8]}_{pdf.doc_id}.pdf"

        return pdf.build(output_path)
