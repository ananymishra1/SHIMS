"""Shared branded page furniture for SHIMS Enterprise PDFs.

Provides a reusable "Page X of Y" canvas with a logo + company header and a
two-column rows renderer. `branded_pdf()` in enterprise_documents delegates here
so every business document (PO, quotation, SOP, lab notebook, GST, deviation,
CAPA, EBR, APQR, …) gets the same regulatory-grade letterhead, table layout and
page numbering as the COA — instead of a flat list of drawString lines.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from .branded_base import COMPANY, _logo_path

_INK = colors.HexColor("#0F172A")
_ACCENT = colors.HexColor("#1E3A8A")
_HEAD_BG = colors.HexColor("#DCE3F4")
_GRID = colors.HexColor("#64748B")
_MUTED = colors.HexColor("#475569")
_ZEBRA = colors.HexColor("#F1F5F9")

_USABLE_W = A4[0] - 24 * mm


class BrandedNumberedCanvas(_canvas.Canvas):
    """Canvas that defers page rendering so it can print 'Page X of Y'.

    Reads presentation data from a `brand` dict: title, company, logo, doc_no,
    footer, draft.
    """

    def __init__(self, *args, **kwargs):
        self._brand = kwargs.pop("brand", {})
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict] = []

    def showPage(self):  # noqa: N802
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            self._draw_header()
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_header(self) -> None:
        b = self._brand
        company = b.get("company", COMPANY)
        logo = b.get("logo")
        top = A4[1] - 12 * mm
        if logo:
            try:
                self.drawImage(str(logo), 12 * mm, top - 16 * mm, width=34 * mm,
                               height=16 * mm, preserveAspectRatio=True, anchor="nw", mask="auto")
            except Exception:
                pass
        cx = A4[0] / 2
        self.setFillColor(_INK)
        self.setFont("Helvetica-Bold", 13)
        self.drawCentredString(cx, top - 3 * mm, company.get("legal_name", "").upper())
        self.setFont("Helvetica", 8)
        self.setFillColor(_MUTED)
        self.drawCentredString(cx, top - 8 * mm, company.get("address", ""))
        bits = [x for x in [
            f"GSTIN: {company['gstin']}" if company.get("gstin") else "",
            f"CIN: {company['cin']}" if company.get("cin") else "",
            company.get("email", ""),
        ] if x]
        if bits:
            self.drawCentredString(cx, top - 12 * mm, "   |   ".join(bits))
        self.setStrokeColor(_ACCENT)
        self.setLineWidth(1.2)
        self.line(12 * mm, top - 15 * mm, A4[0] - 12 * mm, top - 15 * mm)
        self.setFillColor(_ACCENT)
        self.setFont("Helvetica-Bold", 12)
        self.drawCentredString(cx, top - 20 * mm, b.get("title", "").upper())
        if b.get("draft"):
            self.saveState()
            self.setFont("Helvetica-Bold", 60)
            self.setFillColor(colors.HexColor("#FCA5A5"))
            self.translate(cx, A4[1] / 2)
            self.rotate(45)
            self.drawCentredString(0, 0, "DRAFT")
            self.restoreState()

    def _draw_footer(self, total: int) -> None:
        b = self._brand
        company = b.get("company", COMPANY)
        y = 10 * mm
        self.setStrokeColor(_GRID)
        self.setLineWidth(0.4)
        self.line(12 * mm, y + 5 * mm, A4[0] - 12 * mm, y + 5 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(_MUTED)
        left = f"Doc. No.: {b['doc_no']}" if b.get("doc_no") else (b.get("footer") or company.get("website", ""))
        self.drawString(12 * mm, y, str(left)[:90])
        self.drawCentredString(A4[0] / 2, y, f"Issued: {datetime.now().strftime('%d-%b-%Y %H:%M')}")
        self.drawRightString(A4[0] - 12 * mm, y, f"Page {self._pageNumber} of {total}")


def branded_template(output_path: Path, brand: dict[str, Any]):
    """Return (BaseDocTemplate, canvasmaker) wired to the branded furniture."""
    base = BaseDocTemplate(
        str(output_path), pagesize=A4,
        topMargin=36 * mm, bottomMargin=16 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
        title=brand.get("title", "SHIMS Document"),
        author=brand.get("company", COMPANY).get("legal_name", "SHIMS Enterprise"),
    )
    frame = Frame(base.leftMargin, base.bottomMargin, base.width, base.height, id="body")
    base.addPageTemplates([PageTemplate(id="branded", frames=[frame])])

    def _mk(*a, **k):
        return BrandedNumberedCanvas(*a, brand=brand, **k)

    return base, _mk


def _styles() -> dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    return {
        "label": ParagraphStyle("label", parent=ss["BodyText"], fontName="Helvetica-Bold",
                                fontSize=9, leading=12, textColor=_INK),
        "value": ParagraphStyle("value", parent=ss["BodyText"], fontName="Helvetica",
                                fontSize=9, leading=12, textColor=_INK),
        "section": ParagraphStyle("section", parent=ss["BodyText"], fontName="Helvetica-Bold",
                                  fontSize=10, leading=13, textColor=colors.white),
        "note": ParagraphStyle("note", parent=ss["BodyText"], fontName="Helvetica-Oblique",
                               fontSize=8.5, leading=11, textColor=_MUTED),
    }


def render_rows_pdf(
    title: str,
    rows: Sequence[tuple[Any, Any]],
    output_path: Path | str,
    *,
    footer: str = "",
    doc_no: str = "",
    draft: bool = False,
    company: dict | None = None,
) -> Path:
    """Render (label, value) rows as a branded two-column document.

    A row whose value is None renders as a shaded full-width **section band**
    using the label as the section title — so callers can group fields.
    """
    company = company or dict(COMPANY)
    st = _styles()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data: list[list] = []
    style_cmds: list[tuple] = [
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for k, v in rows:
        idx = len(data)
        if v is None:  # section band
            data.append([Paragraph(str(k).upper(), st["section"]), ""])
            style_cmds += [
                ("SPAN", (0, idx), (-1, idx)),
                ("BACKGROUND", (0, idx), (-1, idx), _ACCENT),
            ]
        else:
            data.append([Paragraph(str(k), st["label"]), Paragraph(str(v), st["value"])])
            style_cmds.append(("BACKGROUND", (0, idx), (0, idx), _HEAD_BG))
            if idx % 2 == 1:
                style_cmds.append(("BACKGROUND", (1, idx), (1, idx), _ZEBRA))
    if not data:
        data = [[Paragraph("—", st["label"]), Paragraph("", st["value"])]]

    table = Table(data, colWidths=[58 * mm, _USABLE_W - 58 * mm], repeatRows=0)
    table.setStyle(TableStyle(style_cmds))

    story = [table]
    if footer:
        story += [Spacer(1, 6 * mm), Paragraph(footer, st["note"])]

    brand = {"title": title, "company": company, "logo": _logo_path(),
             "doc_no": doc_no, "footer": footer, "draft": draft}
    base, mk = branded_template(output_path, brand)
    base.build(story, canvasmaker=mk)
    return output_path
