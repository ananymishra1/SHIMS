"""BMR GMP validator: cross-check a BMR against the chemistry corpus and SOPs.

Produces a structured report with critical/major/minor observations, citations,
and recommended corrections. Useful for QA review, tech transfer sign-off, and
GMP readiness assessment.
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


def ensure_bmr_validator_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS bmr_validation_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bmr_id INTEGER NOT NULL,
            status TEXT DEFAULT 'draft',
            overall_score REAL DEFAULT 0.0,
            findings_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


@dataclass
class Finding:
    severity: str  # critical, major, minor, observation
    category: str  # stoichiometry, procedure, controls, materials, safety, documentation, yield
    message: str
    citation: str = ''
    suggested_fix: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'severity': self.severity,
            'category': self.category,
            'message': self.message,
            'citation': self.citation,
            'suggested_fix': self.suggested_fix,
        }


@dataclass
class ValidationReport:
    bmr_id: int
    findings: list[Finding] = field(default_factory=list)
    overall_score: float = 0.0
    status: str = 'draft'

    def to_dict(self) -> dict[str, Any]:
        return {
            'bmr_id': self.bmr_id,
            'overall_score': self.overall_score,
            'status': self.status,
            'findings': [f.to_dict() for f in self.findings],
            'generated_at': datetime.now().isoformat(timespec='seconds'),
        }


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _normalize(text: str | None) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(text or '').lower())


def _extract_numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r'\d+\.?\d*', str(text or ''))]


def get_bmr_record(bmr_id: int) -> dict[str, Any] | None:
    row = db.one('SELECT * FROM bmr_records WHERE id=?', (bmr_id,))
    return dict(row) if row else None


def get_bmr_stages(bmr_id: int) -> list[dict[str, Any]]:
    rows = db.query('SELECT * FROM bmr_stages WHERE bmr_id=? ORDER BY stage_no', (bmr_id,))
    return [dict(r) for r in rows]


def get_bmr_steps(bmr_id: int) -> list[dict[str, Any]]:
    rows = db.query('SELECT * FROM bmr_step_entries WHERE bmr_id=? ORDER BY bmr_stage_id, id', (bmr_id,))
    return [dict(r) for r in rows]


def get_bmr_materials(bmr_id: int) -> list[dict[str, Any]]:
    # bmr_materials table does not exist; derive from stage rm_description and step material_name
    rows = db.query("SELECT rm_description FROM bmr_stages WHERE bmr_id=? AND COALESCE(rm_description,'')!=''", (bmr_id,))
    materials = []
    for r in rows:
        materials.append({'source': 'stage', 'material': r['rm_description']})
    step_rows = db.query("SELECT material_name FROM bmr_step_entries WHERE bmr_id=? AND COALESCE(material_name,'')!=''", (bmr_id,))
    for r in step_rows:
        materials.append({'source': 'step', 'material': r['material_name']})
    return materials


def corpus_facts_for_product(product_name: str) -> list[dict[str, Any]]:
    norm = _normalize(product_name)
    rows = db.query(
        """
        SELECT f.*, d.product_name, d.document_type FROM enterprise_bmr_facts f
        JOIN enterprise_bmr_documents d ON d.id = f.document_id
        WHERE lower(d.product_name) LIKE ? OR lower(d.original_name) LIKE ?
        ORDER BY f.confidence DESC
        """,
        (f'%{product_name.lower()}%', f'%{product_name.lower()}%'),
    )
    return [dict(r) for r in rows]


def corpus_process_map_for_product(product_name: str) -> dict[str, Any] | None:
    norm = _normalize(product_name)
    row = db.one(
        """
        SELECT * FROM enterprise_bmr_process_maps
        WHERE lower(product_name) LIKE ? ORDER BY id DESC LIMIT 1
        """,
        (f'%{product_name.lower()}%',),
    )
    return dict(row) if row else None


def validate_bmr_against_corpus(bmr_id: int, user_id: int | None = None) -> ValidationReport:
    """Run a full validation of a BMR against the chemistry corpus and basic GMP rules."""
    ensure_bmr_validator_schema()
    report = ValidationReport(bmr_id=bmr_id)
    bmr = get_bmr_record(bmr_id)
    if not bmr:
        report.findings.append(Finding('critical', 'documentation', f'BMR {bmr_id} not found'))
        return report

    product_name = str(bmr.get('product_name') or '').strip()
    stages = get_bmr_stages(bmr_id)
    steps = get_bmr_steps(bmr_id)
    materials = get_bmr_materials(bmr_id)

    # 1. Basic structural checks
    if not stages:
        report.findings.append(Finding('critical', 'procedure', 'BMR has no defined stages'))
    if not steps:
        report.findings.append(Finding('critical', 'procedure', 'BMR has no defined steps'))
    if not materials:
        report.findings.append(Finding('major', 'materials', 'BMR has no raw materials listed'))

    stage_names = [str(s.get('stage_name') or '').strip().lower() for s in stages]
    required_api_stages = {'reaction', 'quench', 'extraction', 'wash', 'crystallization', 'drying'}
    missing_stages = required_api_stages - set(stage_names)
    if missing_stages and len(stage_names) > 0:
        report.findings.append(Finding(
            'major', 'procedure',
            f'API BMR is missing typical stages: {", ".join(sorted(missing_stages))}',
            citation='ICH Q7 / API manufacturing good practice',
            suggested_fix='Review route map and add missing unit operations with IPCs.',
        ))

    # 2. Safety/PPE mentions in steps (check step_name + expected_value)
    step_texts = [str(s.get('step_name') or '') + ' ' + str(s.get('expected_value') or '') for s in steps]
    has_ppe_mention = any('ppe' in t.lower() or 'safety' in t.lower() for t in step_texts)
    if not has_ppe_mention:
        report.findings.append(Finding(
            'major', 'safety',
            'No explicit PPE or safety instruction found in BMR steps',
            citation='Site safety and cGMP personnel protection expectations',
            suggested_fix='Add PPE requirements to first step of each hazardous stage.',
        ))

    # 3. Critical parameters documented
    has_temp = any('temperature' in t.lower() or '°c' in t.lower() for t in step_texts)
    has_time = any(re.search(r'\b\d+\s*(min|hr|h|hours)\b', t) for t in step_texts)
    if not has_temp:
        report.findings.append(Finding('minor', 'controls', 'Temperature not explicitly documented in step instructions'))
    if not has_time:
        report.findings.append(Finding('minor', 'controls', 'Reaction/hold times not explicitly documented in step instructions'))

    # 4. Yield expectation
    expected_yield_text = str(bmr.get('expected_yield') or '').strip()
    if not expected_yield_text:
        report.findings.append(Finding('major', 'yield', 'Expected yield is not defined'))
    else:
        nums = _extract_numbers(expected_yield_text)
        if not nums or not (0 < nums[0] <= 200):
            report.findings.append(Finding('major', 'yield', f'Expected yield appears unrealistic or malformed: {expected_yield_text}'))

    # 5. Corpus cross-check
    if product_name:
        process_map = corpus_process_map_for_product(product_name)
        if process_map:
            corpus_stages = _load_json(process_map.get('stages_json'), [])
            corpus_stage_names = {str(s.get('stage') or s.get('name') or '').strip().lower() for s in corpus_stages}
            missing_in_corpus = required_api_stages - corpus_stage_names
            if not missing_in_corpus:
                # Corpus has good coverage
                pass
            else:
                report.findings.append(Finding(
                    'observation', 'procedure',
                    f'Chemistry corpus for {product_name} does not cover stages: {", ".join(sorted(missing_in_corpus))}',
                    citation=f'Corpus process map id={process_map.get("id")}',
                    suggested_fix='Ingest additional reference BMRs covering these stages.',
                ))
            # Check IPC alignment
            corpus_controls = _load_json(process_map.get('controls_json'), [])
            bmr_controls_mentioned = any('ipc' in t.lower() or 'in-process' in t.lower() for t in step_texts)
            if corpus_controls and not bmr_controls_mentioned:
                report.findings.append(Finding(
                    'major', 'controls',
                    f'Corpus lists controls for {product_name}, but BMR does not mention in-process controls',
                    citation=f'Corpus controls: {json.dumps(corpus_controls[:3])}',
                    suggested_fix='Add IPC checks matching corpus controls.',
                ))
        else:
            report.findings.append(Finding(
                'observation', 'procedure',
                f'No chemistry corpus entry found for product: {product_name}',
                citation='BMR corpus learning not yet run or no documents ingested',
                suggested_fix='Upload reference BMRs and run corpus learning.',
            ))

    # 6. Material identity checks
    for m in materials:
        name = str(m.get('material_name') or '').strip()
        qty = str(m.get('quantity') or '').strip()
        uom = str(m.get('uom') or '').strip()
        if name and (not qty or not uom):
            report.findings.append(Finding(
                'major', 'materials',
                f'Material "{name}" is missing quantity or UOM',
                suggested_fix='Record quantity and UOM for every material.',
            ))

    # Score
    weights = {'critical': 0, 'major': 2, 'minor': 4, 'observation': 6}
    if not report.findings:
        report.overall_score = 100.0
    else:
        penalty = sum(weights.get(f.severity, 0) for f in report.findings)
        report.overall_score = max(0.0, 100.0 - penalty * 2.5)

    if report.overall_score >= 95:
        report.status = 'acceptable'
    elif report.overall_score >= 80:
        report.status = 'minor_observations'
    elif report.overall_score >= 60:
        report.status = 'major_observations'
    else:
        report.status = 'not_acceptable'

    # Persist
    db.execute(
        'INSERT INTO bmr_validation_reports(bmr_id, status, overall_score, findings_json, metadata_json, created_by) VALUES (?, ?, ?, ?, ?, ?)',
        (bmr_id, report.status, report.overall_score, json.dumps([f.to_dict() for f in report.findings]), json.dumps({'product_name': product_name}), user_id),
    )
    return report


def get_latest_report(bmr_id: int) -> dict[str, Any] | None:
    ensure_bmr_validator_schema()
    row = db.one('SELECT * FROM bmr_validation_reports WHERE bmr_id=? ORDER BY id DESC LIMIT 1', (bmr_id,))
    if not row:
        return None
    return {
        **dict(row),
        'findings': _load_json(row.get('findings_json'), []),
        'metadata': _load_json(row.get('metadata_json'), {}),
    }


def export_report_pdf(bmr_id: int) -> Path:
    from .document_engine import BrandedPDF, DocumentLine, DocumentSection, FormatConfig
    report_data = get_latest_report(bmr_id)
    if not report_data:
        raise ValueError('No validation report found for BMR')
    bmr = get_bmr_record(bmr_id) or {}
    pdf = BrandedPDF(
        title=f'BMR Validation Report — {bmr.get("product_name", "Unknown")}',
        doc_id=f'BMR-VAL-{bmr_id}-{datetime.now().strftime("%Y%m%d%H%M%S")}',
        kind='qa',
        format_config=FormatConfig(footer_text='Generated by SHIMS BMR Validator'),
    )
    pdf.add_meta('BMR ID', str(bmr_id))
    pdf.add_meta('Product', str(bmr.get('product_name') or '-'))
    pdf.add_meta('Overall Score', f"{report_data.get('overall_score', 0):.1f}")
    pdf.add_meta('Status', report_data.get('status', '-'))
    findings = report_data.get('findings', [])
    for idx, f in enumerate(findings, 1):
        pdf.add_section(DocumentSection(
            title=f'{idx}. [{f.get("severity", "?").upper()}] {f.get("category", "-")}',
            order=idx * 10,
            lines=[
                DocumentLine(key=f'finding_{idx}_msg', label='Observation', value=f.get('message', ''), type='text'),
                DocumentLine(key=f'finding_{idx}_cit', label='Citation', value=f.get('citation', ''), type='text'),
                DocumentLine(key=f'finding_{idx}_fix', label='Suggested Fix', value=f.get('suggested_fix', ''), type='text'),
            ],
        ))
    if not findings:
        pdf.add_section(DocumentSection(
            title='No findings',
            order=10,
            lines=[DocumentLine(key='no_findings', label='Result', value='No findings recorded.', type='text')],
        ))
    path = GENERATED_DIR / f'bmr_validation_report_{bmr_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    return pdf.build(path)
