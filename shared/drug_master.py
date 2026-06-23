"""Drug Master File and India drug license tracking for SHIMS Enterprise.

Tracks:
- Active pharmaceutical ingredients (APIs) and drug products.
- India drug manufacturing licenses (Form 25, 25B, 28, 28B, 29, etc.).
- License amendments, renewals, product additions, and regulatory correspondences.
- EC drug list modification filings and responses.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import GENERATED_DIR, STORAGE_DIR
from .database import db


def ensure_drug_master_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS drug_master_apis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT UNIQUE,
            generic_name TEXT NOT NULL,
            brand_name TEXT,
            dosage_form TEXT,
            strength TEXT,
            manufacturer TEXT,
            site_address TEXT,
            category TEXT DEFAULT 'API',
            status TEXT DEFAULT 'active',
            first_manufactured_date TEXT,
            last_validated_date TEXT,
            dmf_holder TEXT,
            dmf_number TEXT,
            cep_number TEXT,
            usdmf_number TEXT,
            eudmf_number TEXT,
            ind_license_numbers TEXT,
            approved_markets TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS drug_master_licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_number TEXT NOT NULL,
            license_form TEXT NOT NULL,
            issuing_authority TEXT,
            issue_date TEXT,
            expiry_date TEXT,
            status TEXT DEFAULT 'active',
            api_ids TEXT DEFAULT '[]',
            product_names TEXT,
            site_address TEXT,
            scope_summary TEXT,
            renewal_due_date TEXT,
            renewal_filed_date TEXT,
            renewal_status TEXT,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS drug_master_filing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_type TEXT NOT NULL,
            reference_number TEXT,
            authority TEXT,
            api_ids TEXT DEFAULT '[]',
            product_names TEXT,
            status TEXT DEFAULT 'draft',
            submission_date TEXT,
            response_date TEXT,
            response_summary TEXT,
            effective_date TEXT,
            attachments_json TEXT DEFAULT '[]',
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS drug_master_api_license_links (
            api_id INTEGER NOT NULL,
            license_id INTEGER NOT NULL,
            PRIMARY KEY (api_id, license_id)
        )
        """
    )


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


def _generate_product_code(name: str, category: str = 'API') -> str:
    prefix = 'API' if category.upper() == 'API' else 'FG'
    safe = re.sub(r'[^A-Za-z0-9]+', '', name)[:6].upper()
    ts = datetime.now().strftime('%y%m%d%H%M%S')
    return f'{prefix}-{safe}-{ts}'


def list_apis(status: str | None = None, search: str | None = None) -> list[dict[str, Any]]:
    where = ['1=1']
    params: list[Any] = []
    if status:
        where.append('status=?')
        params.append(status)
    if search:
        where.append('(lower(generic_name) LIKE ? OR lower(brand_name) LIKE ? OR lower(product_code) LIKE ?)')
        term = f'%{search}%'
        params.extend([term, term, term])
    rows = db.query(f"SELECT * FROM drug_master_apis WHERE {' AND '.join(where)} ORDER BY generic_name", tuple(params))
    return [_hydrate_api(r) for r in rows]


def _hydrate_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(row),
        'approved_markets': _load_json(row.get('approved_markets'), []),
        'metadata': _load_json(row.get('metadata_json'), {}),
    }


def get_api(api_id: int) -> dict[str, Any] | None:
    row = db.one('SELECT * FROM drug_master_apis WHERE id=?', (api_id,))
    return _hydrate_api(row) if row else None


def create_api(data: dict[str, Any], user_id: int | None = None) -> int:
    ensure_drug_master_schema()
    name = str(data.get('generic_name') or '').strip()
    if not name:
        raise ValueError('generic_name is required')
    category = str(data.get('category') or 'API').strip()
    code = str(data.get('product_code') or '').strip() or _generate_product_code(name, category)
    now = _now()
    fields = {
        'product_code': code,
        'generic_name': name,
        'brand_name': str(data.get('brand_name') or '').strip(),
        'dosage_form': str(data.get('dosage_form') or '').strip(),
        'strength': str(data.get('strength') or '').strip(),
        'manufacturer': str(data.get('manufacturer') or '').strip(),
        'site_address': str(data.get('site_address') or '').strip(),
        'category': category,
        'status': str(data.get('status') or 'active').strip(),
        'first_manufactured_date': str(data.get('first_manufactured_date') or '').strip(),
        'last_validated_date': str(data.get('last_validated_date') or '').strip(),
        'dmf_holder': str(data.get('dmf_holder') or '').strip(),
        'dmf_number': str(data.get('dmf_number') or '').strip(),
        'cep_number': str(data.get('cep_number') or '').strip(),
        'usdmf_number': str(data.get('usdmf_number') or '').strip(),
        'eudmf_number': str(data.get('eudmf_number') or '').strip(),
        'ind_license_numbers': str(data.get('ind_license_numbers') or '').strip(),
        'approved_markets': _json(data.get('approved_markets', [])),
        'metadata_json': _json(data.get('metadata', {})),
        'created_at': now,
        'updated_at': now,
    }
    cols = ', '.join(fields.keys())
    placeholders = ', '.join(['?'] * len(fields))
    return db.execute(f'INSERT INTO drug_master_apis ({cols}) VALUES ({placeholders})', tuple(fields.values()))


def update_api(api_id: int, data: dict[str, Any]) -> bool:
    row = db.one('SELECT id FROM drug_master_apis WHERE id=?', (api_id,))
    if not row:
        raise ValueError('API not found')
    allowed = {
        'generic_name', 'brand_name', 'dosage_form', 'strength', 'manufacturer',
        'site_address', 'category', 'status', 'first_manufactured_date',
        'last_validated_date', 'dmf_holder', 'dmf_number', 'cep_number',
        'usdmf_number', 'eudmf_number', 'ind_license_numbers',
    }
    sets = []
    params: list[Any] = []
    for key in allowed:
        if key in data:
            sets.append(f'{key}=?')
            params.append(str(data[key]).strip())
    if 'approved_markets' in data:
        sets.append('approved_markets=?')
        params.append(_json(data['approved_markets']))
    if 'metadata' in data:
        sets.append('metadata_json=?')
        params.append(_json(data['metadata']))
    if not sets:
        return False
    sets.append('updated_at=?')
    params.append(_now())
    params.append(api_id)
    db.execute(f"UPDATE drug_master_apis SET {', '.join(sets)} WHERE id=?", tuple(params))
    return True


def list_licenses(status: str | None = None, form: str | None = None, expiring_within_days: int | None = None) -> list[dict[str, Any]]:
    where = ['1=1']
    params: list[Any] = []
    if status:
        where.append('status=?')
        params.append(status)
    if form:
        where.append('license_form=?')
        params.append(form)
    if expiring_within_days is not None:
        cutoff = (datetime.now() + timedelta(days=expiring_within_days)).isoformat()[:10]
        where.append('(expiry_date IS NOT NULL AND expiry_date <= ?)')
        params.append(cutoff)
    rows = db.query(f"SELECT * FROM drug_master_licenses WHERE {' AND '.join(where)} ORDER BY expiry_date, license_number", tuple(params))
    return [_hydrate_license(r) for r in rows]


def _hydrate_license(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(row),
        'api_ids': _load_json(row.get('api_ids'), []),
        'metadata': _load_json(row.get('metadata_json'), {}),
    }


def get_license(license_id: int) -> dict[str, Any] | None:
    row = db.one('SELECT * FROM drug_master_licenses WHERE id=?', (license_id,))
    return _hydrate_license(row) if row else None


def create_license(data: dict[str, Any]) -> int:
    ensure_drug_master_schema()
    number = str(data.get('license_number') or '').strip()
    form = str(data.get('license_form') or '').strip()
    if not number or not form:
        raise ValueError('license_number and license_form are required')
    now = _now()
    fields = {
        'license_number': number,
        'license_form': form,
        'issuing_authority': str(data.get('issuing_authority') or '').strip(),
        'issue_date': str(data.get('issue_date') or '').strip(),
        'expiry_date': str(data.get('expiry_date') or '').strip(),
        'status': str(data.get('status') or 'active').strip(),
        'api_ids': _json(data.get('api_ids', [])),
        'product_names': str(data.get('product_names') or '').strip(),
        'site_address': str(data.get('site_address') or '').strip(),
        'scope_summary': str(data.get('scope_summary') or '').strip(),
        'renewal_due_date': str(data.get('renewal_due_date') or '').strip(),
        'renewal_filed_date': str(data.get('renewal_filed_date') or '').strip(),
        'renewal_status': str(data.get('renewal_status') or '').strip(),
        'metadata_json': _json(data.get('metadata', {})),
        'created_at': now,
        'updated_at': now,
    }
    cols = ', '.join(fields.keys())
    placeholders = ', '.join(['?'] * len(fields))
    lid = db.execute(f'INSERT INTO drug_master_licenses ({cols}) VALUES ({placeholders})', tuple(fields.values()))
    for api_id in _load_json(fields['api_ids'], []):
        try:
            db.execute('INSERT OR IGNORE INTO drug_master_api_license_links(api_id, license_id) VALUES (?, ?)', (api_id, lid))
        except Exception:
            pass
    return lid


def update_license(license_id: int, data: dict[str, Any]) -> bool:
    row = db.one('SELECT id FROM drug_master_licenses WHERE id=?', (license_id,))
    if not row:
        raise ValueError('License not found')
    allowed = {
        'license_number', 'license_form', 'issuing_authority', 'issue_date',
        'expiry_date', 'status', 'product_names', 'site_address', 'scope_summary',
        'renewal_due_date', 'renewal_filed_date', 'renewal_status',
    }
    sets = []
    params: list[Any] = []
    for key in allowed:
        if key in data:
            sets.append(f'{key}=?')
            params.append(str(data[key]).strip())
    if 'api_ids' in data:
        sets.append('api_ids=?')
        params.append(_json(data['api_ids']))
        db.execute('DELETE FROM drug_master_api_license_links WHERE license_id=?', (license_id,))
        for api_id in data['api_ids']:
            try:
                db.execute('INSERT OR IGNORE INTO drug_master_api_license_links(api_id, license_id) VALUES (?, ?)', (api_id, license_id))
            except Exception:
                pass
    if 'metadata' in data:
        sets.append('metadata_json=?')
        params.append(_json(data['metadata']))
    if not sets:
        return False
    sets.append('updated_at=?')
    params.append(_now())
    params.append(license_id)
    db.execute(f"UPDATE drug_master_licenses SET {', '.join(sets)} WHERE id=?", tuple(params))
    return True


def list_filing_events(filing_type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    where = ['1=1']
    params: list[Any] = []
    if filing_type:
        where.append('filing_type=?')
        params.append(filing_type)
    if status:
        where.append('status=?')
        params.append(status)
    rows = db.query(f"SELECT * FROM drug_master_filing_events WHERE {' AND '.join(where)} ORDER BY submission_date DESC, created_at DESC", tuple(params))
    return [_hydrate_filing(r) for r in rows]


def _hydrate_filing(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(row),
        'api_ids': _load_json(row.get('api_ids'), []),
        'attachments': _load_json(row.get('attachments_json'), []),
    }


def get_filing_event(event_id: int) -> dict[str, Any] | None:
    row = db.one('SELECT * FROM drug_master_filing_events WHERE id=?', (event_id,))
    return _hydrate_filing(row) if row else None


def create_filing_event(data: dict[str, Any], user_id: int | None = None) -> int:
    ensure_drug_master_schema()
    ftype = str(data.get('filing_type') or '').strip()
    if not ftype:
        raise ValueError('filing_type is required')
    now = _now()
    fields = {
        'filing_type': ftype,
        'reference_number': str(data.get('reference_number') or '').strip(),
        'authority': str(data.get('authority') or '').strip(),
        'api_ids': _json(data.get('api_ids', [])),
        'product_names': str(data.get('product_names') or '').strip(),
        'status': str(data.get('status') or 'draft').strip(),
        'submission_date': str(data.get('submission_date') or '').strip(),
        'response_date': str(data.get('response_date') or '').strip(),
        'response_summary': str(data.get('response_summary') or '').strip(),
        'effective_date': str(data.get('effective_date') or '').strip(),
        'attachments_json': _json(data.get('attachments', [])),
        'notes': str(data.get('notes') or '').strip(),
        'created_by': user_id,
        'created_at': now,
        'updated_at': now,
    }
    cols = ', '.join(fields.keys())
    placeholders = ', '.join(['?'] * len(fields))
    return db.execute(f'INSERT INTO drug_master_filing_events ({cols}) VALUES ({placeholders})', tuple(fields.values()))


def update_filing_event(event_id: int, data: dict[str, Any]) -> bool:
    row = db.one('SELECT id FROM drug_master_filing_events WHERE id=?', (event_id,))
    if not row:
        raise ValueError('Filing event not found')
    allowed = {
        'reference_number', 'authority', 'product_names', 'status',
        'submission_date', 'response_date', 'response_summary', 'effective_date', 'notes',
    }
    sets = []
    params: list[Any] = []
    for key in allowed:
        if key in data:
            sets.append(f'{key}=?')
            params.append(str(data[key]).strip())
    if 'api_ids' in data:
        sets.append('api_ids=?')
        params.append(_json(data['api_ids']))
    if 'attachments' in data:
        sets.append('attachments_json=?')
        params.append(_json(data['attachments']))
    if not sets:
        return False
    sets.append('updated_at=?')
    params.append(_now())
    params.append(event_id)
    db.execute(f"UPDATE drug_master_filing_events SET {', '.join(sets)} WHERE id=?", tuple(params))
    return True


def dashboard_summary() -> dict[str, Any]:
    ensure_drug_master_schema()
    total_apis = db.one('SELECT COUNT(*) AS c FROM drug_master_apis')['c']
    active_apis = db.one("SELECT COUNT(*) AS c FROM drug_master_apis WHERE status='active'")['c']
    total_licenses = db.one('SELECT COUNT(*) AS c FROM drug_master_licenses')['c']
    active_licenses = db.one("SELECT COUNT(*) AS c FROM drug_master_licenses WHERE status='active'")['c']
    expiring_90 = db.one(
        "SELECT COUNT(*) AS c FROM drug_master_licenses WHERE status='active' AND expiry_date IS NOT NULL AND expiry_date <= ?",
        ((datetime.now() + timedelta(days=90)).isoformat()[:10],),
    )['c']
    filings = db.one('SELECT COUNT(*) AS c FROM drug_master_filing_events')['c']
    pending_filings = db.one("SELECT COUNT(*) AS c FROM drug_master_filing_events WHERE status IN ('draft','submitted','under_review')")['c']
    return {
        'total_apis': total_apis,
        'active_apis': active_apis,
        'total_licenses': total_licenses,
        'active_licenses': active_licenses,
        'licenses_expiring_90_days': expiring_90,
        'total_filings': filings,
        'pending_filings': pending_filings,
    }


def seed_common_forms() -> None:
    """Create placeholder licenses for common India CDSCO forms if none exist."""
    ensure_drug_master_schema()
    forms = ['Form 25', 'Form 25B', 'Form 28', 'Form 28B', 'Form 29']
    for form in forms:
        existing = db.one('SELECT id FROM drug_master_licenses WHERE license_form=? LIMIT 1', (form,))
        if existing:
            continue
        create_license({
            'license_number': f'TBD-{form.replace(" ", "")}',
            'license_form': form,
            'issuing_authority': 'CDSCO / State FDA (India)',
            'status': 'placeholder',
            'scope_summary': f'License category {form} — update with actual number, issue date, and expiry.',
        })


def export_drug_master_summary(format_type: str = 'json') -> dict[str, Any] | Path:
    ensure_drug_master_schema()
    apis = list_apis()
    licenses = list_licenses()
    filings = list_filing_events()
    payload = {'apis': apis, 'licenses': licenses, 'filings': filings, 'generated_at': _now()}
    if format_type == 'json':
        return payload
    if format_type == 'pdf':
        from .document_engine import BrandedPDF, DocumentLine, DocumentSection, FormatConfig
        pdf = BrandedPDF(
            title='Drug Master & License Summary',
            doc_id=f'DMF-SUMMARY-{_now().replace(":","")}',
            kind='regulatory',
            format_config=FormatConfig(footer_text='Generated by SHIMS Drug Master'),
        )
        pdf.add_section(DocumentSection(
            title='APIs',
            order=10,
            lines=[DocumentLine(key=f'api_{a["id"]}', label=a.get('generic_name') or 'Unnamed', value=json.dumps(a, default=str), type='text') for a in apis],
        ))
        pdf.add_section(DocumentSection(
            title='Licenses',
            order=20,
            lines=[DocumentLine(key=f'lic_{l["id"]}', label=l.get('license_number') or 'Unnamed', value=json.dumps(l, default=str), type='text') for l in licenses],
        ))
        pdf.add_section(DocumentSection(
            title='Filings',
            order=30,
            lines=[DocumentLine(key=f'fil_{f["id"]}', label=f.get('filing_type') or 'Unnamed', value=json.dumps(f, default=str), type='text') for f in filings],
        ))
        path = GENERATED_DIR / f'drug_master_summary_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
        return pdf.build(path)
    raise ValueError('format_type must be json or pdf')
