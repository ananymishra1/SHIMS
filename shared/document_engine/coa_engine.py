"""High-quality Certificate of Analysis (COA) engine using BrandedPDF."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .branded_base import BrandedPDF, DocumentLine, DocumentSection, FormatConfig, COMPANY
from .template_manager import DocumentTemplateManager
from .preview_engine import PreviewEngine


class COAEngine:
    """Generate pharmaceutical COA PDFs with full branding and quality formatting."""

    @staticmethod
    def generate(
        product_name: str,
        batch_number: str,
        manufacturer: str,
        test_results: list[dict[str, Any]],
        template_id: str | None = None,
        additional_meta: dict | None = None,
        output_path: Path | str | None = None,
        draft: bool = False,
    ) -> Path:
        """Generate a COA PDF from test results.

        Args:
            product_name: Name of the pharmaceutical product
            batch_number: Manufacturing batch/lot number
            manufacturer: Manufacturer name
            test_results: List of dicts with keys: test, result, specification, method
            template_id: Optional custom template ID
            additional_meta: Extra metadata fields
            output_path: Optional explicit output path
            draft: If True, applies DRAFT watermark
        """
        # Load template or use defaults
        if template_id:
            tpl = DocumentTemplateManager.get_template(template_id)
            if not tpl:
                raise ValueError(f"Template {template_id} not found")
            format_config = FormatConfig(**tpl.get("format_config", {}))
            company = tpl.get("company") or COMPANY
        else:
            format_config = FormatConfig(
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
            )
            company = COMPANY

        if draft:
            format_config.watermark_text = "DRAFT — NOT FOR RELEASE"
            format_config.watermark_color = "#FECACA"

        # Build document
        doc_id = f"COA-{batch_number}"
        pdf = BrandedPDF(
            title="Certificate of Analysis",
            doc_id=doc_id,
            kind="coa",
            format_config=format_config,
            company=company,
        )

        # Metadata section
        pdf.add_meta("Product Name", product_name)
        pdf.add_meta("Batch / Lot No.", batch_number)
        pdf.add_meta("Manufacturer", manufacturer)
        pdf.add_meta("Date of Analysis", datetime.now().strftime("%d-%b-%Y"))
        pdf.add_meta("Report Date", datetime.now().strftime("%d-%b-%Y"))
        if additional_meta:
            for k, v in additional_meta.items():
                pdf.add_meta(k, v)

        # Build test results as table lines
        table_lines = [
            DocumentLine(
                key="header",
                label="Test Parameter",
                value="Result",
                type="table",
                bold=True,
            ),
        ]

        for tr in test_results:
            result_str = str(tr.get("result", ""))
            spec_str = str(tr.get("specification", ""))
            method_str = str(tr.get("method", ""))
            unit_str = str(tr.get("unit", ""))
            table_lines.append(DocumentLine(
                key=tr.get("test", "").lower().replace(" ", "_"),
                label=tr.get("test", ""),
                value=f"{result_str} {unit_str}".strip(),
                spec=spec_str,
                unit=method_str,
                type="text",
                required=True,
            ))

        pdf.add_section(DocumentSection(
            title="Analytical Test Results",
            lines=table_lines,
            order=0,
        ))

        # Remarks / conclusion
        remarks = additional_meta.get("remarks", "") if additional_meta else ""
        if not remarks:
            remarks = f"The above batch of {product_name} complies with the specifications."
        pdf.add_section(DocumentSection(
            title="Remarks / Conclusion",
            lines=[DocumentLine(key="remarks", label="Conclusion", value=remarks, type="text")],
            order=1,
        ))

        # Build and return
        if output_path is None:
            slug = batch_number.replace("/", "_")
            output_path = Path("generated") / f"COA_{slug}_{'DRAFT' if draft else 'FINAL'}.pdf"

        return pdf.build(output_path)

    @staticmethod
    def generate_from_form_data(
        form_data: dict[str, Any],
        template_id: str | None = None,
        draft: bool = False,
        output_path: Path | str | None = None,
    ) -> Path:
        """Generate COA from flat form data dict (keys match template line keys)."""
        product = form_data.get("product_name", "Unknown Product")
        batch = form_data.get("batch_number", "N/A")
        mfr = form_data.get("manufacturer", COMPANY.get("trade_name", ""))

        # Build test results from form data
        test_results = []
        if template_id:
            tpl = DocumentTemplateManager.get_template(template_id)
            if tpl:
                for ln in tpl.get("lines", []):
                    key = ln.get("key", "")
                    if key in ("header", "remarks"):
                        continue
                    val = form_data.get(key, "")
                    if val:
                        test_results.append({
                            "test": ln.get("label", key),
                            "result": val,
                            "specification": ln.get("spec", ""),
                            "method": "",
                            "unit": ln.get("unit", ""),
                        })

        if not test_results:
            # Fallback: scan form_data for standard pharma fields
            std_fields = [
                "description", "appearance", "solubility", "identification_ir",
                "identification_uv", "identification_hplc", "melting_point",
                "loss_on_drying", "sulphated_ash", "assay", "ph",
                "optical_rotation", "related_substances", "heavy_metals",
                "residual_solvents", "microbial_limit", "bulk_density",
                "particle_size", "water_content", "chromatographic_purity",
            ]
            for key in std_fields:
                if key in form_data and form_data[key]:
                    test_results.append({
                        "test": key.replace("_", " ").title(),
                        "result": form_data[key],
                        "specification": "",
                        "method": "",
                        "unit": "",
                    })

        return COAEngine.generate(
            product_name=product,
            batch_number=batch,
            manufacturer=mfr,
            test_results=test_results,
            template_id=template_id,
            additional_meta=form_data,
            draft=draft,
            output_path=output_path,
        )

    @staticmethod
    def preview(form_data: dict[str, Any], template_id: str | None = None, output_path: Path | str | None = None) -> Path:
        """Generate a DRAFT preview COA."""
        return COAEngine.generate_from_form_data(form_data, template_id=template_id, draft=True, output_path=output_path)

    @staticmethod
    def finalize(form_data: dict[str, Any], template_id: str | None = None, output_path: Path | str | None = None) -> Path:
        """Generate the final COA (no DRAFT watermark)."""
        return COAEngine.generate_from_form_data(form_data, template_id=template_id, draft=False, output_path=output_path)
