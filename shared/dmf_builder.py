"""DMF (Drug Master File) builder for SHIMS Enterprise.

Type II API DMF structure:
- Open Part (Applicant's Part): holder, site, LoA, general info.
- Closed Part (Restricted Part): chemistry, manufacturing, controls, stability, batch data.

The builder is deterministic and offline. It can auto-fill from:
- drug_master_apis
- rd_experiments + rd_experiment_stages
- enterprise_products + enterprise_product_route_stages
- LIMS / COA data when linked.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import GENERATED_DIR
from .database import db


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def ensure_dmf_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS dmf_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dmf_number TEXT UNIQUE,
            api_name TEXT NOT NULL,
            holder_name TEXT,
            holder_address TEXT,
            site_address TEXT,
            status TEXT DEFAULT 'draft',
            source_type TEXT,
            source_id INTEGER,
            open_part_json TEXT DEFAULT '{}',
            closed_part_json TEXT DEFAULT '{}',
            dossier_html TEXT,
            file_path TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS dmf_section_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part TEXT NOT NULL,
            section_key TEXT NOT NULL,
            title TEXT NOT NULL,
            required INTEGER DEFAULT 0,
            prompt TEXT,
            UNIQUE(part, section_key)
        )
        """
    )
    _seed_section_templates()


def _seed_section_templates() -> None:
    existing = db.one('SELECT COUNT(*) AS c FROM dmf_section_templates')
    if existing and existing['c'] > 0:
        return
    templates = [
        ('open', 'cover_letter', 'Cover Letter / Letter of Authorization', 1, 'Authorization for FDA to reference this DMF; applicant name and address.'),
        ('open', 'administrative', 'Administrative Information', 1, 'DMF holder, contact, facility address, manufacturing site.'),
        ('open', 'general_information', 'General Information', 1, 'Drug substance nomenclature, structure, molecular formula, CAS.'),
        ('closed', 'manufacture', 'Manufacture of the Drug Substance', 1, 'Flow chart, process description, starting materials, critical steps, controls.'),
        ('closed', 'characterization', 'Characterization of the Drug Substance', 1, 'Elucidation of structure, stereochemistry, impurities, physico-chemical properties.'),
        ('closed', 'control_of_materials', 'Control of Materials', 1, 'Starting materials, raw materials, solvents, reagents specifications.'),
        ('closed', 'controls', 'Control of Drug Substance', 1, 'Specification, analytical procedures, validation, batch analysis, justification of specs.'),
        ('closed', 'reference_standards', 'Reference Standards or Materials', 0, 'Source and qualification of reference standards.'),
        ('closed', 'container_closure', 'Container Closure System', 1, 'Description and specifications of packaging materials.'),
        ('closed', 'stability', 'Stability of Drug Substance', 1, 'Stability summary, protocols, results, retest period, storage conditions.'),
    ]
    for part, key, title, req, prompt in templates:
        db.execute(
            'INSERT INTO dmf_section_templates (part, section_key, title, required, prompt) VALUES (?, ?, ?, ?, ?)',
            (part, key, title, req, prompt),
        )


def _generate_dmf_number(api_name: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9]+', '', api_name)[:6].upper()
    ts = datetime.now().strftime('%y%m%d%H%M%S%f')
    return f'DMF-{safe}-{ts}'


def _default_open_part(api_name: str, holder_name: str = '', holder_address: str = '', site_address: str = '') -> dict[str, Any]:
    return {
        'cover_letter': {
            'subject': f'Drug Master File for {api_name}',
            'to_agency': 'US FDA / Health Canada / EDQM / CDSCO',
            'holder_name': holder_name,
            'holder_address': holder_address,
            'authorization': f'{holder_name} authorizes named applicants to reference this DMF in support of their applications.',
        },
        'administrative': {
            'dmf_holder': holder_name,
            'contact_person': '',
            'phone_email': '',
            'manufacturing_site': site_address,
            'responsible_party': '',
        },
        'general_information': {
            'international_nonproprietary_name': api_name,
            'chemical_name': '',
            'cas_number': '',
            'molecular_formula': '',
            'molecular_weight': '',
            'structure_smiles': '',
            'description': '',
        },
    }


def _default_closed_part() -> dict[str, Any]:
    return {
        'manufacture': {
            'flow_chart': '',
            'process_description': '',
            'starting_materials': [],
            'critical_steps': [],
            'process_controls': [],
        },
        'characterization': {
            'structure_elucidation': '',
            'stereochemistry': '',
            'impurities': [],
            'physicochemical_properties': {},
        },
        'control_of_materials': {
            'starting_material_specifications': [],
            'solvent_specifications': [],
            'reagent_specifications': [],
        },
        'controls': {
            'drug_substance_specification': [],
            'analytical_procedures': '',
            'method_validation': '',
            'batch_analysis': [],
            'justification_of_specification': '',
        },
        'reference_standards': {
            'source': '',
            'qualification': '',
        },
        'container_closure': {
            'primary_container': '',
            'specifications': '',
        },
        'stability': {
            'summary': '',
            'protocol': '',
            'results': '',
            'retest_period': '',
            'storage_conditions': '',
        },
    }


def _fetch_product_data(api_name: str) -> dict[str, Any]:
    """Try to auto-fill from product / R&D tables."""
    data: dict[str, Any] = {}
    try:
        rows = db.query(
            "SELECT * FROM enterprise_products WHERE lower(product_name) LIKE ? ORDER BY id DESC LIMIT 1",
            (f'%{api_name.lower()}%',),
        )
        if rows:
            data['product'] = dict(rows[0])
    except Exception:
        pass
    try:
        rows = db.query(
            "SELECT * FROM rd_experiments WHERE lower(product_name) LIKE ? OR lower(title) LIKE ? ORDER BY updated_at DESC LIMIT 1",
            (f'%{api_name.lower()}%', f'%{api_name.lower()}%'),
        )
        if rows:
            exp = dict(rows[0])
            exp_id = exp.get('id')
            if exp_id:
                stages = db.query('SELECT * FROM rd_experiment_stages WHERE experiment_id=? ORDER BY stage_order, id', (exp_id,))
                exp['stages'] = [dict(s) for s in stages]
            data['experiment'] = exp
    except Exception:
        pass
    return data


def _apply_autofill(open_part: dict[str, Any], closed_part: dict[str, Any], api_name: str) -> None:
    product_data = _fetch_product_data(api_name)
    prod = product_data.get('product') or {}
    exp = product_data.get('experiment') or {}
    stages = exp.get('stages') or []

    # General info
    open_part['general_information']['international_nonproprietary_name'] = api_name
    if prod.get('cas_number'):
        open_part['general_information']['cas_number'] = prod['cas_number']
    if prod.get('molecular_formula'):
        open_part['general_information']['molecular_formula'] = prod['molecular_formula']
    if prod.get('smiles'):
        open_part['general_information']['structure_smiles'] = prod['smiles']
    if prod.get('description'):
        open_part['general_information']['description'] = prod['description']

    # Manufacturing
    if stages:
        closed_part['manufacture']['flow_chart'] = ' -> '.join(
            s.get('stage_name') or f"Stage {i+1}" for i, s in enumerate(stages)
        )
        closed_part['manufacture']['process_description'] = '\n'.join(
            f"{s.get('stage_name')}: {s.get('procedure') or s.get('notes') or 'No description'}" for s in stages
        )
        rms: list[str] = []
        for s in stages:
            for rm in s.get('raw_materials') or []:
                if isinstance(rm, dict):
                    name = rm.get('name') or rm.get('material_name') or ''
                    qty = rm.get('quantity') or rm.get('qty') or ''
                    unit = rm.get('unit') or 'kg'
                    if name:
                        rms.append(f"{name} ({qty} {unit})".strip())
                elif isinstance(rm, str):
                    rms.append(rm)
        if rms:
            closed_part['control_of_materials']['starting_material_specifications'] = [
                {'name': name, 'specification': 'To be defined', 'supplier': ''} for name in sorted(set(rms))
            ]

    # Impurities from product
    impurities = prod.get('impurities') or prod.get('known_impurities')
    if impurities:
        if isinstance(impurities, str):
            impurities = [impurities]
        closed_part['characterization']['impurities'] = [
            {'name': name, 'origin': 'process-related', 'control': 'included in DS specification'}
            for name in impurities
        ]


def create_dmf(data: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    ensure_dmf_schema()
    api_name = str(data.get('api_name') or '').strip()
    if not api_name:
        raise ValueError('api_name is required')
    holder_name = str(data.get('holder_name') or '').strip()
    holder_address = str(data.get('holder_address') or '').strip()
    site_address = str(data.get('site_address') or '').strip()
    dmf_number = str(data.get('dmf_number') or '').strip() or _generate_dmf_number(api_name)

    open_part = _load_json(data.get('open_part_json'), None) or _default_open_part(api_name, holder_name, holder_address, site_address)
    closed_part = _load_json(data.get('closed_part_json'), None) or _default_closed_part()

    if data.get('autofill', True):
        _apply_autofill(open_part, closed_part, api_name)

    now = _now()
    did = db.execute(
        """INSERT INTO dmf_records
        (dmf_number, api_name, holder_name, holder_address, site_address, status, source_type, source_id, open_part_json, closed_part_json, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            dmf_number, api_name, holder_name, holder_address, site_address,
            'draft', data.get('source_type'), data.get('source_id'),
            _json(open_part), _json(closed_part), user_id, now, now,
        ),
    )
    return {'ok': True, 'dmf_id': did, 'dmf_number': dmf_number, 'api_name': api_name}


def get_dmf(dmf_id: int) -> dict[str, Any] | None:
    ensure_dmf_schema()
    row = db.one('SELECT * FROM dmf_records WHERE id=?', (dmf_id,))
    if not row:
        return None
    return _hydrate_dmf(row)


def get_dmf_by_number(dmf_number: str) -> dict[str, Any] | None:
    ensure_dmf_schema()
    row = db.one('SELECT * FROM dmf_records WHERE dmf_number=?', (dmf_number,))
    if not row:
        return None
    return _hydrate_dmf(row)


def _hydrate_dmf(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(row),
        'open_part': _load_json(row.get('open_part_json'), {}),
        'closed_part': _load_json(row.get('closed_part_json'), {}),
    }


def list_dmfs(api_name: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    ensure_dmf_schema()
    where = ['1=1']
    params: list[Any] = []
    if api_name:
        where.append('lower(api_name) LIKE ?')
        params.append(f'%{api_name.lower()}%')
    if status:
        where.append('status=?')
        params.append(status)
    rows = db.query(f"SELECT * FROM dmf_records WHERE {' AND '.join(where)} ORDER BY updated_at DESC", tuple(params))
    return [_hydrate_dmf(r) for r in rows]


def update_dmf(dmf_id: int, data: dict[str, Any]) -> bool:
    ensure_dmf_schema()
    row = db.one('SELECT id FROM dmf_records WHERE id=?', (dmf_id,))
    if not row:
        raise ValueError('DMF not found')
    sets = []
    params: list[Any] = []
    if 'open_part' in data:
        sets.append('open_part_json=?')
        params.append(_json(data['open_part']))
    if 'closed_part' in data:
        sets.append('closed_part_json=?')
        params.append(_json(data['closed_part']))
    if 'status' in data:
        sets.append('status=?')
        params.append(str(data['status']).strip())
    if not sets:
        return False
    sets.append('updated_at=?')
    params.append(_now())
    params.append(dmf_id)
    db.execute(f"UPDATE dmf_records SET {', '.join(sets)} WHERE id=?", tuple(params))
    return True


def _section_html(title: str, content: Any) -> str:
    body: str
    if isinstance(content, dict):
        body = '<table>' + ''.join(f'<tr><td><strong>{k}</strong></td><td>{v}</td></tr>' for k, v in content.items()) + '</table>'
    elif isinstance(content, list):
        body = '<ul>' + ''.join(f'<li>{item}</li>' for item in content) + '</ul>' if content else '<p>No entries.</p>'
    else:
        body = f'<p>{content or "No information provided."}</p>'
    return f'<section><h2>{title}</h2>{body}</section>'


def render_dmf_dossier(dmf_id: int) -> dict[str, Any]:
    dmf = get_dmf(dmf_id)
    if not dmf:
        raise ValueError('DMF not found')
    templates = {f"{t['part']}/{t['section_key']}": t for t in db.query('SELECT * FROM dmf_section_templates')}
    open_part = dmf.get('open_part') or {}
    closed_part = dmf.get('closed_part') or {}
    sections_html = []
    for key, value in open_part.items():
        t = templates.get(f'open/{key}')
        title = t['title'] if t else key.replace('_', ' ').title()
        sections_html.append(_section_html(title, value))
    for key, value in closed_part.items():
        t = templates.get(f'closed/{key}')
        title = t['title'] if t else key.replace('_', ' ').title()
        sections_html.append(_section_html(title, value))
    html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>DMF {dmf['dmf_number']} — {dmf['api_name']}</title>
<style>
body {{ font-family: Arial, sans-serif; line-height: 1.6; max-width: 900px; margin: 40px auto; padding: 20px; }}
h1 {{ color: #1a365d; border-bottom: 2px solid #cbd5e1; }}
h2 {{ color: #334155; margin-top: 28px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
th, td {{ border: 1px solid #94a3b8; padding: 8px; text-align: left; }}
th {{ background: #f1f5f9; }}
</style></head>
<body>
<h1>Drug Master File — Type II API</h1>
<p><strong>DMF Number:</strong> {dmf['dmf_number']}<br>
<strong>API:</strong> {dmf['api_name']}<br>
<strong>Holder:</strong> {dmf['holder_name'] or '—'}<br>
<strong>Site:</strong> {dmf['site_address'] or '—'}<br>
<strong>Status:</strong> {dmf['status']}</p>
{''.join(sections_html)}
</body></html>"""
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', dmf['api_name'])[:40]
    path: Path = GENERATED_DIR / f"dmf_dossier_{safe}_{dmf['dmf_number']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(html, encoding='utf-8')
    db.execute('UPDATE dmf_records SET dossier_html=?, file_path=?, updated_at=? WHERE id=?', (html, str(path), _now(), dmf_id))
    return {'ok': True, 'dmf_id': dmf_id, 'file_path': str(path), 'download_url': f'/generated/{path.name}'}


def dmf_gap_analysis(dmf_id: int) -> dict[str, Any]:
    dmf = get_dmf(dmf_id)
    if not dmf:
        raise ValueError('DMF not found')
    templates = db.query('SELECT * FROM dmf_section_templates')
    open_part = dmf.get('open_part') or {}
    closed_part = dmf.get('closed_part') or {}
    gaps: list[dict[str, Any]] = []
    for t in templates:
        part = t['part']
        key = t['section_key']
        section = (open_part if part == 'open' else closed_part).get(key)
        filled = bool(section)
        if isinstance(section, dict):
            filled = any(v not in (None, '', [], {}) for v in section.values())
        if t['required'] and not filled:
            gaps.append({'part': part, 'section': key, 'title': t['title'], 'severity': 'required_missing'})
        elif not filled:
            gaps.append({'part': part, 'section': key, 'title': t['title'], 'severity': 'optional_empty'})
    score = max(0, 100 - len([g for g in gaps if g['severity'] == 'required_missing']) * 10)
    return {'ok': True, 'dmf_id': dmf_id, 'score': score, 'gaps': gaps}
