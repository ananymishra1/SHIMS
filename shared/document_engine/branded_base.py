"""Unified branded PDF base class for ALL SHIMS documents.
Ensures consistent logo, header, footer, watermarks across every PDF.
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, PageTemplate, Paragraph, Spacer,
    Table, TableStyle, KeepTogether
)

from shared.config import GENERATED_DIR, ROOT_DIR
from shared.security import new_id

COMPANY = {
    "legal_name": "J.K. LIFECARE CENTERS PRIVATE LIMITED",
    "trade_name": "J.K. LIFECARE CENTERS PRIVATE LIMITED",
    "gstin": "23AAECJ6427F1ZS",
    "address": "Plot No 97, DMIC VUL, Ujjain, Madhya Pradesh, India - 456664",
    "state_code": "23",
    "phone": "+91 7000452122",
    "email": "info@jklifecarecenters.com",
    "website": "www.jklifecarecenters.com",
    "cin": "U24239MP2019PTC048507",
    "bank_name": "ICICI Bank",
    "bank_account_holder": "J.K. Lifecare Centers Pvt Ltd",
    "bank_account_no": "658505603306",
    "bank_account_type": "Current Account",
    "bank_ifsc": "ICIC0006585",
}

LOGO_CANDIDATES = [
    ROOT_DIR / "shims_enterprise" / "static" / "jk_logo.png",
    ROOT_DIR / "frontend" / "jk_logo.png",
]


def _logo_path() -> Path | None:
    for p in LOGO_CANDIDATES:
        if p.exists():
            return p
    return None


@dataclass
class DocumentLine:
    """A single line/field in a document template."""
    key: str
    label: str
    value: str = ""
    type: str = "text"  # text | number | date | table | subheader | signature | spacer
    required: bool = False
    spec: str = ""  # specification / reference range
    unit: str = ""
    order: int = 0
    indent: int = 0  # 0 = main line, 1+ = subline
    font_size: int = 10
    bold: bool = False
    color: str = "#000000"
    width_pct: float = 100.0  # width percentage of page
    children: list[DocumentLine] = field(default_factory=list)


@dataclass
class DocumentSection:
    """A section containing multiple lines."""
    title: str
    lines: list[DocumentLine] = field(default_factory=list)
    order: int = 0
    page_break_before: bool = False
    bg_color: str | None = None


@dataclass
class FormatConfig:
    """Document-wide formatting configuration."""
    page_size: str = "A4"
    orientation: str = "portrait"
    margin_top: float = 20.0
    margin_bottom: float = 20.0
    margin_left: float = 20.0
    margin_right: float = 20.0
    header_font: str = "Helvetica-Bold"
    header_font_size: int = 16
    body_font: str = "Helvetica"
    body_font_size: int = 10
    table_header_bg: str = "#E2E8F0"
    table_line_bg: str = "#F8FAFC"
    table_border_color: str = "#CBD5E1"
    primary_color: str = "#0F172A"
    accent_color: str = "#2563EB"
    watermark_text: str = ""
    watermark_color: str = "#E2E8F0"
    show_logo: bool = True
    show_page_numbers: bool = True
    show_company_header: bool = True
    show_footer: bool = True
    footer_text: str = ""
    signature_lines: int = 3


class BrandedPDF:
    """Base class for all SHIMS branded PDFs.

    Usage:
        pdf = BrandedPDF(title="COA Report", doc_id="COA-001")
        pdf.add_section(DocumentSection(title="Test Results", lines=[...]))
        path = pdf.build()
    """

    def __init__(
        self,
        title: str,
        doc_id: str = "",
        kind: str = "document",
        format_config: FormatConfig | None = None,
        company: dict[str, str] | None = None,
        logo_path: Path | str | None = None,
    ):
        self.title = title
        self.doc_id = doc_id or new_id("doc")
        self.kind = kind
        self.config = format_config or FormatConfig()
        self.company = company or COMPANY.copy()
        self.logo_path = Path(logo_path) if logo_path else _logo_path()
        self.sections: list[DocumentSection] = []
        self.meta: dict[str, Any] = {}
        self._styles = self._init_styles()

    def _init_styles(self) -> dict[str, ParagraphStyle]:
        ss = getSampleStyleSheet()
        styles = {
            "title": ParagraphStyle(
                "BrandedTitle",
                parent=ss["Heading1"],
                fontSize=self.config.header_font_size,
                fontName=self.config.header_font,
                textColor=colors.HexColor(self.config.primary_color),
                spaceAfter=12,
                alignment=TA_CENTER,
            ),
            "section": ParagraphStyle(
                "BrandedSection",
                parent=ss["Heading2"],
                fontSize=12,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor(self.config.accent_color),
                spaceAfter=8,
                spaceBefore=12,
            ),
            "body": ParagraphStyle(
                "BrandedBody",
                parent=ss["BodyText"],
                fontSize=self.config.body_font_size,
                fontName=self.config.body_font,
                textColor=colors.HexColor(self.config.primary_color),
                leading=14,
            ),
            "spec": ParagraphStyle(
                "BrandedSpec",
                parent=ss["BodyText"],
                fontSize=9,
                fontName="Helvetica-Oblique",
                textColor=colors.HexColor("#64748B"),
                leading=12,
            ),
            "footer": ParagraphStyle(
                "BrandedFooter",
                parent=ss["Normal"],
                fontSize=8,
                fontName="Helvetica",
                textColor=colors.HexColor("#94A3B8"),
                alignment=TA_CENTER,
            ),
            "watermark": ParagraphStyle(
                "BrandedWatermark",
                parent=ss["Normal"],
                fontSize=60,
                fontName="Helvetica-Bold",
                textColor=colors.HexColor(self.config.watermark_color),
                alignment=TA_CENTER,
            ),
        }
        return styles

    def add_section(self, section: DocumentSection) -> None:
        self.sections.append(section)
        self.sections.sort(key=lambda s: s.order)

    def add_meta(self, key: str, value: Any) -> None:
        self.meta[key] = value

    def _header_content(self, canvas, doc):
        """Draw header on every page."""
        canvas.saveState()
        y = A4[1] - 15 * mm

        # Logo
        if self.config.show_logo and self.logo_path and self.logo_path.exists():
            try:
                canvas.drawImage(
                    str(self.logo_path), 20 * mm, y - 15 * mm,
                    width=50 * mm, height=15 * mm,
                    preserveAspectRatio=True, anchor="nw", mask="auto"
                )
            except Exception:
                pass

        # Company header block (right side)
        if self.config.show_company_header:
            canvas.setFont("Helvetica-Bold", 9)
            canvas.setFillColor(colors.HexColor(self.config.primary_color))
            canvas.drawRightString(A4[0] - 20 * mm, y, self.company.get("trade_name", ""))
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#64748B"))
            y -= 4 * mm
            canvas.drawRightString(A4[0] - 20 * mm, y, f"GSTIN: {self.company.get('gstin', '')}")
            y -= 4 * mm
            canvas.drawRightString(A4[0] - 20 * mm, y, self.company.get("address", ""))
            y -= 4 * mm
            canvas.drawRightString(A4[0] - 20 * mm, y, f"Ph: {self.company.get('phone', '')} | {self.company.get('email', '')}")

        # Red accent line
        canvas.setStrokeColor(colors.HexColor(self.config.accent_color))
        canvas.setLineWidth(1.5)
        canvas.line(20 * mm, A4[1] - 35 * mm, A4[0] - 20 * mm, A4[1] - 35 * mm)

        # Document title banner
        canvas.setFillColor(colors.HexColor(self.config.accent_color))
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(A4[0] / 2, A4[1] - 42 * mm, self.title.upper())

        # Doc ID
        if self.doc_id:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#94A3B8"))
            canvas.drawRightString(A4[0] - 20 * mm, A4[1] - 42 * mm, f"Ref: {self.doc_id}")

        canvas.restoreState()

    def _footer_content(self, canvas, doc):
        """Draw footer on every page."""
        canvas.saveState()
        y = 15 * mm

        # Grey line
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, y + 6 * mm, A4[0] - 20 * mm, y + 6 * mm)

        # Footer text
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#94A3B8"))

        left_text = f"Generated: {datetime.now().strftime('%d-%b-%Y %H:%M')}"
        center_text = self.config.footer_text or self.company.get("website", "")
        right_text = f"Page {doc.page}"

        canvas.drawString(20 * mm, y, left_text)
        canvas.drawCentredString(A4[0] / 2, y, center_text)
        if self.config.show_page_numbers:
            canvas.drawRightString(A4[0] - 20 * mm, y, right_text)

        # Watermark
        if self.config.watermark_text:
            canvas.setFont("Helvetica-Bold", 48)
            canvas.setFillColor(colors.HexColor(self.config.watermark_color))
            canvas.saveState()
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, self.config.watermark_text.upper())
            canvas.restoreState()

        canvas.restoreState()

    def _build_table_from_lines(self, lines: list[DocumentLine]) -> Table:
        """Build a ReportLab Table from DocumentLine objects."""
        data = []
        row_styles: list[tuple] = []
        for line in lines:
            row_idx = len(data)
            label = str(line.label or "")
            value = str(line.value or "")
            prefix = "&nbsp;" * max(0, int(line.indent or 0) * 4)
            if line.type in {"header", "subheader"}:
                data.append([
                    Paragraph(f"<b>{prefix}{label}</b>", self._styles["section"]),
                    "",
                    "",
                    "",
                ])
                row_styles.append(("SPAN", (0, row_idx), (-1, row_idx)))
                row_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor(self.config.table_header_bg)))
            elif line.type == "spacer":
                data.append(["", "", "", ""])
            elif line.type == "signature":
                data.append([
                    Paragraph(label, self._styles["body"]),
                    "",
                    "_____________________",
                    f"Date: {datetime.now().strftime('%d-%b-%Y')}"
                ])
            elif line.type in {"list", "task", "subtask"}:
                marker = "[ ] " if line.type in {"task", "subtask"} else "- "
                data.append([
                    Paragraph(f"{prefix}{marker}<b>{label}</b>", self._styles["body"]),
                    Paragraph(value, self._styles["body"]),
                    Paragraph(f"<i>{line.spec}</i>" if line.spec else "", self._styles["spec"]),
                    Paragraph(line.unit, self._styles["body"]),
                ])
            elif line.type == "footer":
                data.append([
                    Paragraph(f"<i>{prefix}{label} {value}</i>", self._styles["spec"]),
                    "",
                    "",
                    "",
                ])
                row_styles.append(("SPAN", (0, row_idx), (-1, row_idx)))
            elif line.type == "table" and line.value:
                # Parse JSON table data
                try:
                    rows = json.loads(line.value) if isinstance(line.value, str) else line.value
                    if rows:
                        data.extend(rows)
                except Exception:
                    data.append([line.label, line.value])
            else:
                # Standard key-value row with spec
                label_cell = Paragraph(f"<b>{prefix}{label}</b>", self._styles["body"])
                value_cell = Paragraph(value, self._styles["body"])
                spec_cell = Paragraph(f"<i>Spec: {line.spec}</i>" if line.spec else "", self._styles["spec"])
                unit_cell = Paragraph(line.unit, self._styles["body"])
                data.append([label_cell, value_cell, spec_cell, unit_cell])

        if not data:
            data = [["", "", "", ""]]

        # Determine column widths based on content
        col_widths = [60 * mm, 60 * mm, 40 * mm, 20 * mm]
        if len(data[0]) == 1:
            col_widths = [A4[0] - 40 * mm]

        table = Table(data, colWidths=col_widths, repeatRows=1)
        style_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(self.config.table_header_bg)),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(self.config.primary_color)),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTNAME", (0, 0), (-1, -1), self.config.body_font),
            ("FONTSIZE", (0, 0), (-1, -1), self.config.body_font_size),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(self.config.table_border_color)),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]
        # Alternate row colors
        for i in range(1, len(data)):
            if i % 2 == 0:
                style_commands.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(self.config.table_line_bg)))
        style_commands.extend(row_styles)

        table.setStyle(TableStyle(style_commands))
        return table

    def _build_story(self) -> list:
        """Build the platypus story (flowables) from sections."""
        story: list = []

        # Meta info table (if any)
        if self.meta:
            meta_data = [[Paragraph("<b>Field</b>", self._styles["body"]), Paragraph("<b>Value</b>", self._styles["body"])]]
            for k, v in self.meta.items():
                meta_data.append([Paragraph(str(k).replace("_", " ").title(), self._styles["body"]), Paragraph(str(v), self._styles["body"])])
            meta_table = Table(meta_data, colWidths=[50 * mm, 110 * mm])
            meta_table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(self.config.table_border_color)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(self.config.table_header_bg)),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(meta_table)
            story.append(Spacer(1, 8 * mm))

        # Sections
        for section in self.sections:
            if section.page_break_before:
                story.append(KeepTogether([Spacer(1, 1 * mm)]))  # Force page break hint

            if section.title:
                story.append(Paragraph(section.title, self._styles["section"]))

            if section.lines:
                table = self._build_table_from_lines(section.lines)
                story.append(table)

            story.append(Spacer(1, 6 * mm))

        # Signature block
        if self.config.signature_lines > 0:
            story.append(Spacer(1, 10 * mm))
            # Render exactly one Prepared/Reviewed/Approved block (3 signatories).
            sig_data = [
                [
                    Paragraph("<b>Prepared By</b>", self._styles["body"]),
                    "",
                    Paragraph("<b>Reviewed By</b>", self._styles["body"]),
                    "",
                    Paragraph("<b>Approved By</b>", self._styles["body"]),
                ],
                [
                    "_____________________",
                    "",
                    "_____________________",
                    "",
                    "_____________________",
                ],
                [
                    "Name & Date",
                    "",
                    "Name & Date",
                    "",
                    "Name & Date",
                ],
            ]
            sig_table = Table(sig_data, colWidths=[45 * mm, 10 * mm, 45 * mm, 10 * mm, 45 * mm])
            sig_table.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]))
            story.append(sig_table)

        return story

    def build(self, output_path: Path | str | None = None) -> Path:
        """Build the PDF and return the file path."""
        if output_path is None:
            slug = hashlib.sha256(self.title.encode()).hexdigest()[:8]
            output_path = GENERATED_DIR / f"{self.kind}_{slug}_{self.doc_id}.pdf"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        doc = BaseDocTemplate(
            str(output_path),
            pagesize=A4,
            topMargin=self.config.margin_top * mm,
            bottomMargin=self.config.margin_bottom * mm,
            leftMargin=self.config.margin_left * mm,
            rightMargin=self.config.margin_right * mm,
        )

        frame = Frame(
            doc.leftMargin, doc.bottomMargin + 20 * mm,
            doc.width, doc.height - 50 * mm,
            id="normal"
        )

        template = PageTemplate(
            id="branded",
            frames=[frame],
            onPage=self._header_content,
            onPageEnd=self._footer_content,
        )
        doc.addPageTemplates([template])

        story = self._build_story()
        doc.build(story)
        return output_path

    def to_bytes(self) -> bytes:
        """Build PDF to memory buffer and return bytes."""
        buf = io.BytesIO()
        # Build directly to buffer using our own doc template
        from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
        doc = BaseDocTemplate(
            buf,
            pagesize=A4,
            topMargin=self.config.margin_top * mm,
            bottomMargin=self.config.margin_bottom * mm,
            leftMargin=self.config.margin_left * mm,
            rightMargin=self.config.margin_right * mm,
        )
        frame = Frame(
            doc.leftMargin, doc.bottomMargin + 20 * mm,
            doc.width, doc.height - 50 * mm,
            id="normal"
        )
        template = PageTemplate(
            id="branded",
            frames=[frame],
            onPage=self._header_content,
            onPageEnd=self._footer_content,
        )
        doc.addPageTemplates([template])
        story = self._build_story()
        doc.build(story)
        return buf.getvalue()
