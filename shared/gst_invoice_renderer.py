from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.pdfgen import canvas as _canvas

from .document_engine.branded_base import COMPANY, _logo_path

_INK = colors.HexColor("#0F172A")
_ACCENT = colors.HexColor("#1E3A8A")
_LIGHT = colors.HexColor("#EFF6FF")
_MUTED = colors.HexColor("#475569")
_GRID = colors.HexColor("#CBD5E1")

_MARGIN_X = 10 * mm
_USABLE_W = A4[0] - 2 * _MARGIN_X


class _NumberedCanvas(_canvas.Canvas):
    def __init__(self, *args, **kwargs):
        self._brand = kwargs.pop("brand", {})
        super().__init__(*args, **kwargs)
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._draw_header()
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_header(self) -> None:
        b = self._brand
        company = b.get("company", COMPANY)
        logo = b.get("logo")
        top = A4[1] - 8 * mm
        if logo:
            try:
                self.drawImage(str(logo), _MARGIN_X, top - 10 * mm, width=48 * mm,
                               height=16 * mm, preserveAspectRatio=True, anchor="nw", mask="auto")
            except Exception:
                pass
        cx = A4[0] / 2
        self.setFillColor(_INK)
        self.setFont("Helvetica-Bold", 15)
        self.drawCentredString(cx, top - 8 * mm, company.get("legal_name", "").upper())
        self.setFont("Helvetica", 9)
        self.setFillColor(_MUTED)
        self.drawCentredString(cx, top - 13 * mm, company.get("address", ""))
        bits = [x for x in [
            f"GSTIN: {company['gstin']}" if company.get("gstin") else "",
            company.get("email", ""),
            company.get("phone", ""),
        ] if x]
        if bits:
            self.drawCentredString(cx, top - 17 * mm, "   |   ".join(bits))
        self.setFont("Helvetica-Bold", 9)
        self.setFillColor(_ACCENT)
        self.drawCentredString(cx, top - 21 * mm, "Committed to Quality, Innovation & Patient Safety")
        self.setStrokeColor(_ACCENT)
        self.setLineWidth(1.5)
        self.line(_MARGIN_X, top - 23 * mm, A4[0] - _MARGIN_X, top - 23 * mm)
        self.setFillColor(_ACCENT)
        self.setFont("Helvetica-Bold", 14)
        self.drawCentredString(cx, top - 28 * mm, b.get("title", "").upper())
        if b.get("draft"):
            self.saveState()
            self.setFont("Helvetica-Bold", 55)
            self.setFillColor(colors.HexColor("#FCA5A5"))
            self.translate(cx, A4[1] / 2)
            self.rotate(45)
            self.drawCentredString(0, 0, "DRAFT")
            self.restoreState()

    def _draw_footer(self, total: int) -> None:
        b = self._brand
        y = 6 * mm
        self.setStrokeColor(_GRID)
        self.setLineWidth(0.5)
        self.line(_MARGIN_X, y + 5 * mm, A4[0] - _MARGIN_X, y + 5 * mm)
        self.setFont("Helvetica", 8)
        self.setFillColor(_MUTED)
        left = f"Doc. No.: {b.get('doc_no', '')}"
        self.drawString(_MARGIN_X, y, left[:90])
        self.drawCentredString(A4[0] / 2, y, b.get("footer_text", "This is a computer-generated draft. Verify before filing/submission."))
        self.drawRightString(A4[0] - _MARGIN_X, y, f"Page {self._pageNumber} of {total}")


def _template(output_path: Path, brand: dict[str, Any]):
    base = BaseDocTemplate(
        str(output_path), pagesize=A4,
        topMargin=38 * mm, bottomMargin=14 * mm,
        leftMargin=_MARGIN_X, rightMargin=_MARGIN_X,
        title=brand.get("title", "SHIMS Document"),
        author=brand.get("company", COMPANY).get("legal_name", "SHIMS Enterprise"),
    )
    frame = Frame(base.leftMargin, base.bottomMargin, base.width, base.height, id="body")
    base.addPageTemplates([PageTemplate(id="branded", frames=[frame])])
    return base, lambda *a, **k: _NumberedCanvas(*a, brand=brand, **k)


def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=_INK),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica", fontSize=9, leading=11, textColor=_INK),
        "body_b": ParagraphStyle("body_b", parent=ss["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11, textColor=_INK),
        "muted": ParagraphStyle("muted", parent=ss["BodyText"], fontName="Helvetica", fontSize=8, leading=10, textColor=_MUTED),
        "right": ParagraphStyle("right", parent=ss["BodyText"], fontName="Helvetica", fontSize=9, leading=11, alignment=2, textColor=_INK),
        "right_b": ParagraphStyle("right_b", parent=ss["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=2, textColor=_INK),
    }


def _p(style: str, text: Any) -> Paragraph:
    return Paragraph(str(text or ""), _styles()[style])


def _amount_in_words(num: float) -> str:
    """Convert a number to Indian-rupee words (lakhs/crores)."""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _two(n: int) -> str:
        if n < 20:
            return ones[n]
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")

    def _three(n: int) -> str:
        if n < 100:
            return _two(n)
        return ones[n // 100] + " Hundred" + (" and " + _two(n % 100) if n % 100 else "")

    n = int(round(num))
    if n == 0:
        return "Zero Rupees Only"
    parts: list[str] = []
    crore = n // 10_000_000
    n %= 10_000_000
    lakh = n // 100_000
    n %= 100_000
    thousand = n // 1_000
    n %= 1_000
    if crore:
        parts.append(_three(crore) + " Crore")
    if lakh:
        parts.append(_three(lakh) + " Lakh")
    if thousand:
        parts.append(_three(thousand) + " Thousand")
    if n:
        if parts:
            parts.append("and " + _three(n))
        else:
            parts.append(_three(n))
    return " ".join(parts) + " Rupees Only"


def render_gst_invoice_pdf(payload: dict[str, Any], output_path: Path | str, draft: bool = True, doc_no: str | None = None) -> Path:
    """Render a polished, regulatory-grade GST tax invoice."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = payload.get("DocDtls", {})
    seller = payload.get("SellerDtls", {})
    buyer = payload.get("BuyerDtls", {})
    val = payload.get("ValDtls", {})
    ewb = payload.get("EwbDtls", {})
    items = payload.get("ItemList", [])

    # Seller / buyer boxes
    seller_data = [
        [_p("h1", "Seller"), _p("h1", "Buyer")],
        [
            _p("body_b", seller.get("LglNm", "")),
            _p("body_b", buyer.get("LglNm", "")),
        ],
        [
            _p("body", seller.get("Addr1", "")),
            _p("body", buyer.get("Addr1", "")),
        ],
        [
            _p("body", f"GSTIN: {seller.get('Gstin', '')}"),
            _p("body", f"GSTIN: {buyer.get('Gstin', '')}"),
        ],
        [
            _p("body", f"State: {seller.get('Stcd', '')} | PIN: {seller.get('Pin', '')}"),
            _p("body", f"State: {buyer.get('Stcd', '')} | PIN: {buyer.get('Pin', '')}"),
        ],
    ]
    seller_table = Table(seller_data, colWidths=[_USABLE_W / 2, _USABLE_W / 2])
    seller_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Invoice meta
    meta_data = [
        [_p("body_b", "Invoice No:"), _p("body", doc.get("No", "")),
         _p("body_b", "Invoice Date:"), _p("body", doc.get("Dt", ""))],
        [_p("body_b", "Place of Supply:"), _p("body", buyer.get("Stcd", "")),
         _p("body_b", "Reverse Charge:"), _p("body", "No")],
    ]
    meta_table = Table(meta_data, colWidths=[28 * mm, 48 * mm, 32 * mm, 48 * mm])
    meta_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (0, -1), _LIGHT),
        ("BACKGROUND", (2, 0), (2, -1), _LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    # Items table — widths sum to _USABLE_W so columns do not run off the page.
    item_header = ["#", "Description", "HSN", "Qty", "Unit", "Rate", "Taxable", "GST%", "CGST", "SGST", "IGST", "Total"]
    item_rows = [item_header]
    for it in items:
        item_rows.append([
            it.get("SlNo", ""),
            _p("body", it.get("PrdDesc", "")),
            it.get("HsnCd", ""),
            f"{it.get('Qty', 0):.2f}",
            it.get("Unit", ""),
            f"{it.get('UnitPrice', 0):,.2f}",
            f"{it.get('AssAmt', 0):,.2f}",
            f"{it.get('GstRt', 0):.2f}",
            f"{it.get('CgstAmt', 0):,.2f}",
            f"{it.get('SgstAmt', 0):,.2f}",
            f"{it.get('IgstAmt', 0):,.2f}",
            f"{it.get('TotItemVal', 0):,.2f}",
        ])
    col_widths = [
        8 * mm,   # #
        37 * mm,  # Description
        14 * mm,  # HSN
        12 * mm,  # Qty
        12 * mm,  # Unit
        16 * mm,  # Rate
        19 * mm,  # Taxable
        11 * mm,  # GST%
        14 * mm,  # CGST
        14 * mm,  # SGST
        14 * mm,  # IGST
        19 * mm,  # Total
    ]
    assert sum(col_widths) <= _USABLE_W + 0.1, f"item column widths exceed page: {sum(col_widths)} > {_USABLE_W}"
    items_table = Table(item_rows, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
    ]))

    # Totals
    total_data = [
        ["", "", _p("right_b", "Taxable Value"), _p("right", f"₹ {val.get('AssVal', 0):,.2f}")],
        ["", "", _p("right_b", "CGST"), _p("right", f"₹ {val.get('CgstVal', 0):,.2f}")],
        ["", "", _p("right_b", "SGST"), _p("right", f"₹ {val.get('SgstVal', 0):,.2f}")],
        ["", "", _p("right_b", "IGST"), _p("right", f"₹ {val.get('IgstVal', 0):,.2f}")],
        ["", "", _p("right_b", "Grand Total"), _p("right_b", f"₹ {val.get('TotInvVal', 0):,.2f}")],
    ]
    total_table = Table(total_data, colWidths=[_USABLE_W - 135 * mm, 45 * mm, 45 * mm, 45 * mm])
    total_table.setStyle(TableStyle([
        ("GRID", (2, 0), (-1, -1), 0.5, _GRID),
        ("BACKGROUND", (2, 0), (2, -2), _LIGHT),
        ("BACKGROUND", (2, -1), (-1, -1), _ACCENT),
        ("TEXTCOLOR", (2, -1), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Amount in words
    words_data = [[
        _p("body_b", "Amount Chargeable (in words):"),
        _p("body", _amount_in_words(val.get("TotInvVal", 0))),
    ]]
    words_table = Table(words_data, colWidths=[55 * mm, _USABLE_W - 55 * mm])
    words_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (0, 0), _LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Bank details for transfer
    bank = {
        **COMPANY,
        **(seller or {}),
    }
    bank_data = [
        [_p("h1", "Bank Details (for NEFT / RTGS / IMPS transfer)"), ""],
        [_p("body_b", "Account Holder"), _p("body", bank.get("bank_account_holder", "—"))],
        [_p("body_b", "Bank Name"), _p("body", bank.get("bank_name", "—"))],
        [_p("body_b", "Account Number"), _p("body", bank.get("bank_account_no", "—"))],
        [_p("body_b", "Account Type"), _p("body", bank.get("bank_account_type", "—"))],
        [_p("body_b", "IFSC Code"), _p("body", bank.get("bank_ifsc", "—"))],
    ]
    bank_table = Table(bank_data, colWidths=[45 * mm, _USABLE_W - 45 * mm])
    bank_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("SPAN", (0, 0), (-1, 0)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 1), (0, -1), _LIGHT),
    ]))

    # Transport / IRN
    notes_data = [
        [_p("body_b", "Transporter"), _p("body", ewb.get("TransName", "—"))],
        [_p("body_b", "Vehicle No"), _p("body", ewb.get("VehNo", "—"))],
        [_p("body_b", "Distance (km)"), _p("body", str(ewb.get("Distance", "—")))],
        [_p("body_b", "IRN / QR"), _p("muted", "Draft only. Live IRP/GSP submission can be enabled after credentials are configured." if draft else "IRN will be generated on submission to IRP/GSP.")],
    ]
    notes_table = Table(notes_data, colWidths=[35 * mm, _USABLE_W - 35 * mm])
    notes_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("BACKGROUND", (0, 0), (0, -1), _LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    # Authorized signatory
    sign_data = [
        ["", _p("right", "For J.K. Lifecare Centers Private Limited")],
        ["", _p("right", "Authorized Signatory")],
    ]
    sign_table = Table(sign_data, colWidths=[_USABLE_W / 2, _USABLE_W / 2])
    sign_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 20),
    ]))

    story = [
        seller_table,
        Spacer(1, 2 * mm),
        meta_table,
        Spacer(1, 2 * mm),
        items_table,
        Spacer(1, 2 * mm),
        total_table,
        Spacer(1, 2 * mm),
        words_table,
        Spacer(1, 2 * mm),
        bank_table,
        Spacer(1, 2 * mm),
        notes_table,
        Spacer(1, 4 * mm),
        sign_table,
    ]

    brand = {
        "title": "Tax Invoice" + (" (Draft)" if draft else ""),
        "company": seller or COMPANY,
        "logo": _logo_path(),
        "doc_no": doc_no or doc.get("No", ""),
        "draft": draft,
        "footer_text": (
            "This is a computer-generated draft. Verify before filing/submission."
            if draft else "Original for Recipient | Thank you for your business."
        ),
    }
    base, mk = _template(output_path, brand)
    base.build(story, canvasmaker=mk)
    return output_path


def render_ewaybill_pdf(payload: dict[str, Any], output_path: Path | str, draft: bool = True) -> Path:
    """Render a polished GST e-Way Bill draft."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ewb = payload.get("EwayBillDtls", {})
    partb = payload.get("PartB", {})
    source = payload.get("SourceInvoice", {}) or {}

    data = [
        [_p("h1", "e-Way Bill Details"), ""],
        ["Document Type", ewb.get("docType", "")],
        ["Document No", ewb.get("docNo", "")],
        ["Document Date", ewb.get("docDate", "")],
        ["Supply Type", ewb.get("supplyType", "")],
        ["Sub Supply Type", ewb.get("subSupplyType", "")],
        ["Transaction Type", ewb.get("transactionType", "")],
        ["From GSTIN", ewb.get("fromGstin", "")],
        ["From Trader", ewb.get("fromTrdName", "")],
        ["From PIN", ewb.get("fromPincode", "")],
        ["To GSTIN", ewb.get("toGstin", "")],
        ["To Trader", ewb.get("toTrdName", "")],
        ["To PIN", ewb.get("toPincode", "")],
        ["", ""],
        [_p("h1", "Part B - Vehicle / Transporter"), ""],
        ["Transporter ID", partb.get("transporterId", "—")],
        ["Transporter Name", partb.get("transporterName", "—")],
        ["Vehicle No", partb.get("vehicleNo", "—")],
        ["Transport Mode", partb.get("transportMode", "—")],
        ["Vehicle Type", partb.get("vehicleType", "—")],
        ["Distance (km)", partb.get("transDistance", "—")],
        ["Trans Doc No", partb.get("transDocNo", "—")],
        ["Trans Doc Date", partb.get("transDocDate", "—")],
    ]
    if source:
        data += [
            ["", ""],
            [_p("h1", "Source Invoice"), ""],
            ["Invoice No", source.get("No", "—")],
            ["Invoice Date", source.get("Dt", "—")],
        ]

    rows = []
    for k, v in data:
        if isinstance(k, Paragraph) and v == "":
            rows.append([k, ""])
        else:
            rows.append([_p("body_b", k), _p("body", v)])

    table = Table(rows, colWidths=[55 * mm, _USABLE_W - 55 * mm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("SPAN", (0, 0), (-1, 0)),
        ("BACKGROUND", (0, 14), (-1, 14), _ACCENT),
        ("TEXTCOLOR", (0, 14), (-1, 14), colors.white),
        ("SPAN", (0, 14), (-1, 14)),
    ]))
    if source:
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 24), (-1, 24), _ACCENT),
            ("TEXTCOLOR", (0, 24), (-1, 24), colors.white),
            ("SPAN", (0, 24), (-1, 24)),
        ]))

    story = [table]
    brand = {
        "title": "e-Way Bill Draft",
        "company": COMPANY,
        "logo": _logo_path(),
        "doc_no": ewb.get("docNo", ""),
        "draft": draft,
    }
    base, mk = _template(output_path, brand)
    base.build(story, canvasmaker=mk)
    return output_path
