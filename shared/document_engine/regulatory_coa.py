"""Regulatory-grade Certificate of Analysis renderer.

This is the single authoritative COA layout for SHIMS Enterprise. It reproduces a
real pharmacopoeial COA (branded header with logo + company block, batch-detail
grid, a grouped SR.NO / TEST PARAMETER / SPECIFICATION / RESULTS table with
shaded section bands, remarks, storage conditions, a Prepared/Reviewed/Approved
signature block, and "Page X of Y" footers).

It is driven by structured data so it works for *any* product, not just the
seeded Fluconazole reference. The COA template fields used across Enterprise
(`label`, `spec`, `method`, `section`) map directly onto the table below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table,
    TableStyle,
)

from shared.config import GENERATED_DIR

from .branded_base import COMPANY, _logo_path

# Brand palette (kept aligned with the rest of the document engine).
_INK = colors.HexColor("#0F172A")
_ACCENT = colors.HexColor("#1E3A8A")
_BAND_BG = colors.HexColor("#1E3A8A")
_BAND_FG = colors.white
_HEAD_BG = colors.HexColor("#DCE3F4")
_GRID = colors.HexColor("#64748B")
_MUTED = colors.HexColor("#475569")

_USABLE_W = A4[0] - 24 * mm  # 12mm margins each side
# SR | TEST PARAMETER | SPECIFICATION | RESULTS
_COL_W = [14 * mm, 64 * mm, 74 * mm, _USABLE_W - (14 + 64 + 74) * mm]


@dataclass
class COATestRow:
    """One analytical parameter line."""
    label: str
    specification: str = ""
    result: str = ""
    method: str = ""
    section: str = "General"


@dataclass
class COADocument:
    """Everything needed to render a regulatory COA."""
    product_name: str
    chemical_name: str = ""
    rows: list[COATestRow] = field(default_factory=list)
    # Batch / sampling metadata (mirrors the reference COA fields).
    batch_no: str = ""
    ar_no: str = ""
    batch_size: str = ""
    pack_size: str = ""
    manufactured_on: str = ""
    retest_date: str = ""
    date_of_sampling: str = ""
    date_of_analysis: str = ""
    storage_conditions: str = (
        "Store in a tightly closed container, protected from direct sunlight, below 30°C."
    )
    remarks: str = ""
    pharmacopoeia: str = "IP"
    doc_no: str = ""
    draft: bool = False
    company: dict[str, str] = field(default_factory=lambda: dict(COMPANY))


class _NumberedCanvas(_canvas.Canvas):
    """Canvas that defers footers so it can print 'Page X of Y'."""

    def __init__(self, *args, **kwargs):
        self._brand = kwargs.pop("brand", {})
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict] = []

    def showPage(self):  # noqa: N802 (reportlab API)
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

    # ── header / footer (drawn on every page) ──────────────────────────────
    def _draw_header(self) -> None:
        company = self._brand.get("company", COMPANY)
        logo = self._brand.get("logo")
        top = A4[1] - 12 * mm
        if logo:
            try:
                self.drawImage(
                    str(logo), 12 * mm, top - 16 * mm, width=34 * mm, height=16 * mm,
                    preserveAspectRatio=True, anchor="nw", mask="auto",
                )
            except Exception:
                pass
        cx = A4[0] / 2
        self.setFillColor(_INK)
        self.setFont("Helvetica-Bold", 13)
        self.drawCentredString(cx, top - 3 * mm, company.get("legal_name", "").upper())
        self.setFont("Helvetica", 8)
        self.setFillColor(_MUTED)
        addr = company.get("address", "")
        self.drawCentredString(cx, top - 8 * mm, addr)
        bits = []
        if company.get("gstin"):
            bits.append(f"GSTIN: {company['gstin']}")
        if company.get("cin"):
            bits.append(f"CIN: {company['cin']}")
        if company.get("email"):
            bits.append(company["email"])
        if bits:
            self.drawCentredString(cx, top - 12 * mm, "   |   ".join(bits))
        # Title band
        self.setStrokeColor(_ACCENT)
        self.setLineWidth(1.2)
        self.line(12 * mm, top - 15 * mm, A4[0] - 12 * mm, top - 15 * mm)
        self.setFillColor(_ACCENT)
        self.setFont("Helvetica-Bold", 12)
        self.drawCentredString(cx, top - 20 * mm, "CERTIFICATE OF ANALYSIS")
        if self._brand.get("draft"):
            self.saveState()
            self.setFont("Helvetica-Bold", 60)
            self.setFillColor(colors.HexColor("#FCA5A5"))
            self.translate(cx, A4[1] / 2)
            self.rotate(45)
            self.drawCentredString(0, 0, "DRAFT")
            self.restoreState()

    def _draw_footer(self, total: int) -> None:
        company = self._brand.get("company", COMPANY)
        y = 10 * mm
        self.setStrokeColor(_GRID)
        self.setLineWidth(0.4)
        self.line(12 * mm, y + 5 * mm, A4[0] - 12 * mm, y + 5 * mm)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(_MUTED)
        doc_no = self._brand.get("doc_no") or ""
        left = f"Doc. No.: {doc_no}" if doc_no else company.get("website", "")
        self.drawString(12 * mm, y, left)
        self.drawCentredString(
            A4[0] / 2, y,
            f"Issued: {datetime.now().strftime('%d-%b-%Y %H:%M')}",
        )
        self.drawRightString(
            A4[0] - 12 * mm, y, f"Page {self._pageNumber} of {total}",
        )


def _styles() -> dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    return {
        "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontName="Helvetica",
                               fontSize=8, leading=10, textColor=_INK),
        "cell_b": ParagraphStyle("cell_b", parent=ss["BodyText"], fontName="Helvetica-Bold",
                                 fontSize=8, leading=10, textColor=_INK),
        "band": ParagraphStyle("band", parent=ss["BodyText"], fontName="Helvetica-Bold",
                               fontSize=8.5, leading=11, textColor=_BAND_FG),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontName="Helvetica",
                                fontSize=7.5, leading=9.5, textColor=_MUTED),
        "meta_l": ParagraphStyle("meta_l", parent=ss["BodyText"], fontName="Helvetica-Bold",
                                 fontSize=8.5, leading=11, textColor=_INK),
        "meta_v": ParagraphStyle("meta_v", parent=ss["BodyText"], fontName="Helvetica",
                                 fontSize=8.5, leading=11, textColor=_INK),
        "h": ParagraphStyle("h", parent=ss["BodyText"], fontName="Helvetica-Bold",
                            fontSize=9, leading=12, textColor=_INK),
    }


def _grouped(rows: Iterable[COATestRow]) -> list[tuple[str, list[COATestRow]]]:
    """Group rows by section, preserving first-seen order."""
    order: list[str] = []
    buckets: dict[str, list[COATestRow]] = {}
    for r in rows:
        sec = (r.section or "General").strip() or "General"
        if sec not in buckets:
            buckets[sec] = []
            order.append(sec)
        buckets[sec].append(r)
    return [(sec, buckets[sec]) for sec in order]


def render_coa(doc: COADocument, output_path: Path | str | None = None) -> Path:
    """Render `doc` to a branded regulatory COA PDF and return the path."""
    st = _styles()
    company = doc.company or dict(COMPANY)

    if output_path is None:
        slug = (doc.batch_no or doc.product_name or "coa").replace("/", "_").replace(" ", "_")
        suffix = "DRAFT" if doc.draft else "FINAL"
        output_path = GENERATED_DIR / f"COA_{slug}_{suffix}.pdf"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    story: list = []

    # ── Batch detail grid ──────────────────────────────────────────────────
    def L(t: str) -> Paragraph: return Paragraph(t, st["meta_l"])
    def V(t: str) -> Paragraph: return Paragraph(t or "&nbsp;", st["meta_v"])

    product_cell = Paragraph(
        f"<b>{doc.product_name}</b>" + (f"<br/>{doc.chemical_name}" if doc.chemical_name else ""),
        st["meta_v"])
    meta_rows = [
        [L("Product Name"), product_cell, "", ""],
        [L("Batch No."), V(doc.batch_no), L("Manufactured on"), V(doc.manufactured_on)],
        [L("Batch Size"), V(doc.batch_size), L("Retest Date"), V(doc.retest_date)],
        [L("Pack Size"), V(doc.pack_size), L("Date of Sampling"), V(doc.date_of_sampling)],
        [L("A.R. No."), V(doc.ar_no), L("Date of Analysis"), V(doc.date_of_analysis)],
        [L("Pharmacopoeia"), V(doc.pharmacopoeia), L("Document No."), V(doc.doc_no)],
    ]
    meta_w = [28 * mm, _USABLE_W / 2 - 28 * mm, 32 * mm, _USABLE_W / 2 - 32 * mm]
    meta_tbl = Table(meta_rows, colWidths=meta_w)
    meta_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (0, -1), _HEAD_BG),
        ("BACKGROUND", (2, 1), (2, -1), _HEAD_BG),
        ("SPAN", (1, 0), (3, 0)),  # product name value spans full width
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 4 * mm))

    # ── Test results table ─────────────────────────────────────────────────
    data: list[list] = [[
        Paragraph("SR. NO.", st["cell_b"]),
        Paragraph("TEST PARAMETER", st["cell_b"]),
        Paragraph("SPECIFICATION", st["cell_b"]),
        Paragraph("RESULTS", st["cell_b"]),
    ]]
    style_cmds: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]

    sr = 0
    ridx = 0  # data row index
    for sec, members in _grouped(doc.rows):
        ridx += 1
        method_hint = ""
        methods = sorted({m.method for m in members if m.method})
        if methods:
            method_hint = f" (by {', '.join(methods)})"
        band = Paragraph(f"{sec.upper()}{method_hint}", st["band"])
        data.append([band, "", "", ""])
        style_cmds += [
            ("SPAN", (0, ridx), (-1, ridx)),
            ("BACKGROUND", (0, ridx), (-1, ridx), _BAND_BG),
        ]
        for m in members:
            ridx += 1
            sr += 1
            data.append([
                Paragraph(str(sr), st["cell"]),
                Paragraph(m.label, st["cell_b"]),
                Paragraph(m.specification or "As per approved specification", st["cell"]),
                Paragraph(m.result or "", st["cell"]),
            ])
            if ridx % 2 == 0:
                style_cmds.append(("BACKGROUND", (0, ridx), (-1, ridx), colors.HexColor("#F1F5F9")))

    table = Table(data, colWidths=_COL_W, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    story.append(Spacer(1, 4 * mm))

    # ── Remarks + storage ──────────────────────────────────────────────────
    remarks = doc.remarks or (
        f"The above batch of {doc.product_name} complies with the laid-down "
        f"{doc.pharmacopoeia} specification."
    )
    info_rows = [
        [Paragraph("<b>Remarks</b>", st["meta_l"]), Paragraph(remarks, st["meta_v"])],
        [Paragraph("<b>Storage Conditions</b>", st["meta_l"]),
         Paragraph(doc.storage_conditions, st["meta_v"])],
    ]
    info_tbl = Table(info_rows, colWidths=[36 * mm, _USABLE_W - 36 * mm])
    info_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (0, -1), _HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 8 * mm))

    # ── Signature block (kept together on one page) ────────────────────────
    sig_head = ["Prepared By", "Reviewed By", "Approved By"]
    sig_rows = [
        [Paragraph(f"<b>{h}</b>", st["h"]) for h in sig_head],
        [Paragraph("Name:", st["small"])] * 3,
        [Paragraph("<br/><br/>Signature &amp; Date:", st["small"])] * 3,
    ]
    sig_w = _USABLE_W / 3
    sig_tbl = Table(sig_rows, colWidths=[sig_w, sig_w, sig_w], rowHeights=[8 * mm, 10 * mm, 16 * mm])
    sig_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(KeepTogether([
        Paragraph("Authorised by Quality Control / Quality Assurance", st["h"]),
        Spacer(1, 2 * mm),
        sig_tbl,
    ]))

    # ── Build ──────────────────────────────────────────────────────────────
    brand = {
        "company": company,
        "logo": _logo_path(),
        "draft": doc.draft,
        "doc_no": doc.doc_no,
    }
    base = BaseDocTemplate(
        str(output_path), pagesize=A4,
        topMargin=36 * mm, bottomMargin=16 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
        title=f"Certificate of Analysis - {doc.product_name}",
        author=company.get("legal_name", "SHIMS Enterprise"),
    )
    frame = Frame(base.leftMargin, base.bottomMargin, base.width, base.height, id="body")
    base.addPageTemplates([PageTemplate(id="coa", frames=[frame])])

    def _mk(*a, **k):
        return _NumberedCanvas(*a, brand=brand, **k)

    base.build(story, canvasmaker=_mk)
    return output_path


def coa_from_fields(
    product_name: str,
    fields: list[dict[str, Any]],
    values: dict[str, Any] | None = None,
    *,
    chemical_name: str = "",
    batch_meta: dict[str, Any] | None = None,
    draft: bool = False,
    doc_no: str = "",
    output_path: Path | str | None = None,
) -> Path:
    """Build a COA from Enterprise COA-template `fields` + recorded `values`.

    `fields` items use keys: key, label, spec, method, section.
    `values` maps field key -> measured result.
    `batch_meta` may contain: batch_no, ar_no, batch_size, pack_size,
    manufactured_on, retest_date, date_of_sampling, date_of_analysis,
    storage_conditions, remarks, pharmacopoeia.
    """
    values = values or {}
    batch_meta = batch_meta or {}
    rows = [
        COATestRow(
            label=f.get("label", f.get("key", "")),
            specification=f.get("spec", ""),
            result=str(values.get(f.get("key", ""), "") or ""),
            method=f.get("method", ""),
            section=f.get("section", "General"),
        )
        for f in fields
        if f.get("key") not in {"header", "remarks"}
    ]
    doc = COADocument(
        product_name=product_name,
        chemical_name=chemical_name,
        rows=rows,
        batch_no=batch_meta.get("batch_no", ""),
        ar_no=batch_meta.get("ar_no", ""),
        batch_size=batch_meta.get("batch_size", ""),
        pack_size=batch_meta.get("pack_size", ""),
        manufactured_on=batch_meta.get("manufactured_on", ""),
        retest_date=batch_meta.get("retest_date", ""),
        date_of_sampling=batch_meta.get("date_of_sampling", ""),
        date_of_analysis=batch_meta.get("date_of_analysis", ""),
        storage_conditions=batch_meta.get(
            "storage_conditions",
            COADocument.__dataclass_fields__["storage_conditions"].default,
        ),
        remarks=batch_meta.get("remarks", ""),
        pharmacopoeia=batch_meta.get("pharmacopoeia", "IP"),
        doc_no=doc_no,
        draft=draft,
    )
    return render_coa(doc, output_path=output_path)
