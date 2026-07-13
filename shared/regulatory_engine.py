"""Regulatory document engine for SHIMS Enterprise.

Generates structured drafts for:
- EU/EC drug list modification submissions
- DMF/CEP holder letters and filing packages
- India CDSCO amendment / renewal correspondence
- Site master file excerpts and quality agreements
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import GENERATED_DIR
from .database import db


def ensure_regulatory_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS regulatory_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,
            title TEXT NOT NULL,
            reference_number TEXT,
            authority TEXT,
            product_names TEXT,
            applicant_name TEXT,
            applicant_address TEXT,
            status TEXT DEFAULT 'draft',
            payload_json TEXT DEFAULT '{}',
            rendered_html TEXT,
            file_path TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


@dataclass
class ECDrugListModification:
    applicant_name: str
    applicant_address: str
    product_name: str
    active_substance: str
    modification_type: str  # addition, deletion, change
    proposed_change_summary: str
    justification: str
    current_entry: str = ''
    proposed_entry: str = ''
    supporting_studies: list[str] = field(default_factory=list)
    bp_release_specification: str = ''
    batches_validated: int = 3
    stability_summary: str = ''

    def render(self) -> str:
        sections = [
            ('Cover Letter', self._cover()),
            ('Administrative Data', self._administrative()),
            ('Product Information', self._product_info()),
            ('Proposed Modification', self._proposed_modification()),
            ('Justification', self._justification()),
            ('Quality Information', self._quality()),
            ('Supporting Documentation', self._supporting_docs()),
            ('Declaration', self._declaration()),
        ]
        lines = [f'<section><h2>{idx}. {title}</h2>\n{body}</section>' for idx, (title, body) in enumerate(sections, 1)]
        joined = '\n'.join(lines)
        return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>EC Drug List Modification — {self.product_name}</title>
<style>
body {{ font-family: Arial, sans-serif; line-height: 1.6; max-width: 900px; margin: 40px auto; padding: 20px; }}
h1 {{ color: #1a365d; border-bottom: 2px solid #cbd5e1; padding-bottom: 8px; }}
h2 {{ color: #334155; margin-top: 28px; }}
.label {{ font-weight: bold; color: #475569; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
th, td {{ border: 1px solid #94a3b8; padding: 8px; text-align: left; }}
th {{ background: #f1f5f9; }}
</style></head>
<body>
<h1>European Commission — Union Register / Drug List Modification</h1>
{joined}
</body></html>"""

    def _cover(self) -> str:
        return f"""
<p><span class='label'>From:</span> {self.applicant_name}<br>
<span class='label'>Address:</span> {self.applicant_address}</p>
<p><span class='label'>Date:</span> {_now()[:10]}</p>
<p><span class='label'>Subject:</span> Application for {self.modification_type} of {self.product_name} ({self.active_substance}) in the Union Register / national drug list.</p>
"""

    def _administrative(self) -> str:
        return f"""
<table>
<tr><th>Field</th><th>Value</th></tr>
<tr><td>Applicant / MAAH</td><td>{self.applicant_name}</td></tr>
<tr><td>Active Substance</td><td>{self.active_substance}</td></tr>
<tr><td>Product Name</td><td>{self.product_name}</td></tr>
<tr><td>Modification Type</td><td>{self.modification_type.title()}</td></tr>
<tr><td>Submission Date</td><td>{_now()[:10]}</td></tr>
</table>
"""

    def _product_info(self) -> str:
        return f"""
<p><span class='label'>Current entry (if applicable):</span><br>{self.current_entry or 'N/A'}</p>
<p><span class='label'>Proposed entry:</span><br>{self.proposed_entry or 'To be confirmed by RA'}</p>
"""

    def _proposed_modification(self) -> str:
        return f"<p>{self.proposed_change_summary}</p>"

    def _justification(self) -> str:
        studies = '<ul>' + ''.join(f'<li>{s}</li>' for s in self.supporting_studies) + '</ul>' if self.supporting_studies else '<p>No specific studies listed.</p>'
        return f"<p>{self.justification}</p>\n<h3>Supporting Studies</h3>\n{studies}"

    def _quality(self) -> str:
        return f"""
<p><span class='label'>BP/Ph. Eur. release specification:</span> {self.bp_release_specification or 'As per current approved specification'}</p>
<p><span class='label'>Consecutive validated batches:</span> {self.batches_validated}</p>
<p><span class='label'>Stability summary:</span> {self.stability_summary or 'Stability data available on request and included in CTD Module 3'}</p>
"""

    def _supporting_docs(self) -> str:
        return """
<ul>
<li>Cover letter and application form</li>
<li>Current approved SmPC and labeling (where applicable)</li>
<li>Quality Overall Summary update</li>
<li>Batch analysis data and certificates of analysis</li>
<li>Stability data summary</li>
<li>Process validation summary / cross-reference to approved dossier</li>
<li>Revised artwork and PIL (if labeling changed)</li>
</ul>
"""

    def _declaration(self) -> str:
        return f"<p>I, the undersigned, declare that the information provided in this application is true and accurate to the best of my knowledge.</p><p>Name / Title: _________________________<br>Signature: _________________________<br>Date: {_now()[:10]}</p>"


@dataclass
class DMFHolderLetter:
    holder_name: str
    holder_address: str
    recipient_agency: str
    api_name: str
    dmf_number: str
    subject: str
    body_paragraphs: list[str] = field(default_factory=list)
    authorizations: list[dict[str, Any]] = field(default_factory=list)

    def render(self) -> str:
        body = '\n'.join(f'<p>{p}</p>' for p in self.body_paragraphs) if self.body_paragraphs else '<p>Please find enclosed the Drug Master File update for the above referenced API.</p>'
        auth_table = ''
        if self.authorizations:
            rows = ''.join(f'<tr><td>{a.get("applicant", "")}</td><td>{a.get("product", "")}</td><td>{a.get("country", "")}</td></tr>' for a in self.authorizations)
            auth_table = f"<h3>Authorized Applicants</h3><table><tr><th>Applicant</th><th>Product</th><th>Country</th></tr>{rows}</table>"
        return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>DMF Letter — {self.api_name}</title>
<style>
body {{ font-family: Arial, sans-serif; line-height: 1.6; max-width: 900px; margin: 40px auto; padding: 20px; }}
h1 {{ color: #1a365d; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
th, td {{ border: 1px solid #94a3b8; padding: 8px; text-align: left; }}
th {{ background: #f1f5f9; }}
</style></head>
<body>
<h1>DMF Holder Letter</h1>
<p><strong>Holder:</strong> {self.holder_name}<br>
<strong>Address:</strong> {self.holder_address}<br>
<strong>Recipient Agency:</strong> {self.recipient_agency}<br>
<strong>API:</strong> {self.api_name}<br>
<strong>DMF Number:</strong> {self.dmf_number}</p>
<p><strong>Subject:</strong> {self.subject}</p>
<hr>
{body}
{auth_table}
<p>Respectfully,<br><br>_________________________<br>Authorized Signatory<br>Date: {_now()[:10]}</p>
</body></html>"""


def create_ec_drug_list_modification(data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    ensure_regulatory_schema()
    mod = ECDrugListModification(
        applicant_name=str(data.get('applicant_name') or ''),
        applicant_address=str(data.get('applicant_address') or ''),
        product_name=str(data.get('product_name') or ''),
        active_substance=str(data.get('active_substance') or ''),
        modification_type=str(data.get('modification_type') or 'addition'),
        proposed_change_summary=str(data.get('proposed_change_summary') or ''),
        justification=str(data.get('justification') or ''),
        current_entry=str(data.get('current_entry') or ''),
        proposed_entry=str(data.get('proposed_entry') or ''),
        supporting_studies=list(data.get('supporting_studies', [])),
        bp_release_specification=str(data.get('bp_release_specification') or ''),
        batches_validated=int(data.get('batches_validated', 3) or 3),
        stability_summary=str(data.get('stability_summary') or ''),
    )
    html = mod.render()
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', mod.product_name)[:40]
    path = GENERATED_DIR / f'ec_drug_list_mod_{safe}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
    path.write_text(html, encoding='utf-8')
    did = db.execute(
        'INSERT INTO regulatory_documents(doc_type, title, reference_number, authority, product_names, applicant_name, applicant_address, status, payload_json, rendered_html, file_path, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            'ec_drug_list_modification',
            f'EC Drug List Modification — {mod.product_name}',
            data.get('reference_number'),
            'European Commission / EMA / National Competent Authority',
            mod.product_name,
            mod.applicant_name,
            mod.applicant_address,
            'draft',
            json.dumps(data, default=str),
            html,
            str(path),
            user_id,
        ),
    )
    return {'ok': True, 'document_id': did, 'file_path': str(path), 'download_url': f'/generated/{path.name}'}


def create_dmf_holder_letter(data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    ensure_regulatory_schema()
    letter = DMFHolderLetter(
        holder_name=str(data.get('holder_name') or ''),
        holder_address=str(data.get('holder_address') or ''),
        recipient_agency=str(data.get('recipient_agency') or ''),
        api_name=str(data.get('api_name') or ''),
        dmf_number=str(data.get('dmf_number') or ''),
        subject=str(data.get('subject') or 'DMF Update / Letter of Authorization'),
        body_paragraphs=list(data.get('body_paragraphs', [])),
        authorizations=list(data.get('authorizations', [])),
    )
    html = letter.render()
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', letter.api_name)[:40]
    path = GENERATED_DIR / f'dmf_letter_{safe}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
    path.write_text(html, encoding='utf-8')
    did = db.execute(
        'INSERT INTO regulatory_documents(doc_type, title, reference_number, authority, product_names, applicant_name, applicant_address, status, payload_json, rendered_html, file_path, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            'dmf_holder_letter',
            f'DMF Holder Letter — {letter.api_name}',
            letter.dmf_number,
            letter.recipient_agency,
            letter.api_name,
            letter.holder_name,
            letter.holder_address,
            'draft',
            json.dumps(data, default=str),
            html,
            str(path),
            user_id,
        ),
    )
    return {'ok': True, 'document_id': did, 'file_path': str(path), 'download_url': f'/generated/{path.name}'}


def list_regulatory_documents(doc_type: str | None = None) -> list[dict[str, Any]]:
    ensure_regulatory_schema()
    where = ['1=1']
    params: list[Any] = []
    if doc_type:
        where.append('doc_type=?')
        params.append(doc_type)
    rows = db.query(f"SELECT * FROM regulatory_documents WHERE {' AND '.join(where)} ORDER BY created_at DESC", tuple(params))
    return [dict(r) for r in rows]


def get_regulatory_document(doc_id: int) -> dict[str, Any] | None:
    ensure_regulatory_schema()
    row = db.one('SELECT * FROM regulatory_documents WHERE id=?', (doc_id,))
    return dict(row) if row else None


def update_document_status(doc_id: int, status: str, user_id: int | None = None) -> bool:
    ensure_regulatory_schema()
    row = db.one('SELECT id FROM regulatory_documents WHERE id=?', (doc_id,))
    if not row:
        return False
    db.execute('UPDATE regulatory_documents SET status=?, updated_at=? WHERE id=?', (status, _now(), doc_id))
    return True


def regulatory_dashboard() -> dict[str, Any]:
    ensure_regulatory_schema()
    total = db.one('SELECT COUNT(*) AS c FROM regulatory_documents')['c']
    by_type = {r['doc_type']: r['c'] for r in db.query('SELECT doc_type, COUNT(*) AS c FROM regulatory_documents GROUP BY doc_type')}
    draft = db.one("SELECT COUNT(*) AS c FROM regulatory_documents WHERE status='draft'")['c']
    submitted = db.one("SELECT COUNT(*) AS c FROM regulatory_documents WHERE status='submitted'")['c']
    return {'total': total, 'by_type': by_type, 'draft': draft, 'submitted': submitted}
