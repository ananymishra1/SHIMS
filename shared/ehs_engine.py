"""EHS Engine for SHIMS Enterprise.

Generates effluent characterization reports, carbon footprint estimates,
and SOx/NOx emission summaries for API manufacturing. Supports India
SPCB/PCB filing formats and EC EHS submission structures.
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


def ensure_ehs_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS ehs_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            product_name TEXT,
            batch_size_kg REAL,
            batches_count INTEGER DEFAULT 1,
            parameters_json TEXT DEFAULT '{}',
            results_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'draft',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


@dataclass
class EffluentStream:
    name: str
    volume_liters_per_batch: float = 0.0
    cod_mg_l: float = 0.0
    bod_mg_l: float = 0.0
    tss_mg_l: float = 0.0
    ph_min: float = 6.5
    ph_max: float = 8.5
    tds_mg_l: float = 0.0
    sulfates_mg_l: float = 0.0
    chlorides_mg_l: float = 0.0
    solvents_detected: list[str] = field(default_factory=list)

    def total_load_kg(self, parameter: str) -> float:
        factor = getattr(self, parameter, 0.0)
        return self.volume_liters_per_batch * factor * 1e-6


@dataclass
class BatchChemistry:
    product_name: str
    batch_size_kg: float = 100.0
    solvent_inputs_kg: dict[str, float] = field(default_factory=dict)
    reagent_inputs_kg: dict[str, float] = field(default_factory=dict)
    expected_waste_kg: float = 0.0
    energy_kwh: float = 0.0

    def process_mass_intensity(self) -> float:
        total_inputs = sum(self.solvent_inputs_kg.values()) + sum(self.reagent_inputs_kg.values()) + self.batch_size_kg
        return total_inputs / max(self.batch_size_kg, 1.0)

    def carbon_kg(self) -> float:
        """Rough kg CO2e per batch from solvents and energy."""
        # Conservative factors: common solvents ~2.5 kg CO2e/kg; grid electricity ~0.7 kg CO2e/kWh
        solvent_carbon = sum(qty * 2.5 for qty in self.solvent_inputs_kg.values())
        energy_carbon = self.energy_kwh * 0.7
        return solvent_carbon + energy_carbon

    def sox_kg(self) -> float:
        """Estimate SOx from sulfur-containing reagents and fuel."""
        sulfur_mass = 0.0
        for name, qty in {**self.solvent_inputs_kg, **self.reagent_inputs_kg}.items():
            lower = name.lower()
            if 'sulfur' in lower or 'sulfate' in lower or 'sulfonic' in lower or 'so2' in lower or 'so3' in lower:
                sulfur_mass += qty * 0.05  # rough 5% sulfur assumption
        return sulfur_mass * 2.0  # SO2 mass factor ~2x S


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _load_json(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def build_effluent_report(
    product_name: str,
    batch_size_kg: float,
    batches_count: int,
    streams: list[EffluentStream],
    period_start: str,
    period_end: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    ensure_ehs_schema()
    totals: dict[str, float] = {}
    stream_reports = []
    for s in streams:
        sr = {
            'name': s.name,
            'volume_liters_per_batch': s.volume_liters_per_batch,
            'volume_total_liters': s.volume_liters_per_batch * batches_count,
            'cod_mg_l': s.cod_mg_l,
            'cod_load_kg': s.total_load_kg('cod_mg_l') * batches_count,
            'bod_mg_l': s.bod_mg_l,
            'bod_load_kg': s.total_load_kg('bod_mg_l') * batches_count,
            'tds_mg_l': s.tds_mg_l,
            'tds_load_kg': s.total_load_kg('tds_mg_l') * batches_count,
            'ph_range': f'{s.ph_min}-{s.ph_max}',
            'solvents_detected': s.solvents_detected,
        }
        stream_reports.append(sr)
        for key in ('cod_load_kg', 'bod_load_kg', 'tds_load_kg'):
            totals[key] = totals.get(key, 0.0) + sr[key]

    parameters = {
        'product_name': product_name,
        'batch_size_kg': batch_size_kg,
        'batches_count': batches_count,
        'period_start': period_start,
        'period_end': period_end,
        'streams': [s.__dict__ for s in streams],
    }
    results = {'streams': stream_reports, 'totals': totals}
    rid = db.execute(
        'INSERT INTO ehs_reports(report_type, period_start, period_end, product_name, batch_size_kg, batches_count, parameters_json, results_json, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('effluent', period_start, period_end, product_name, batch_size_kg, batches_count, json.dumps(parameters), json.dumps(results), user_id),
    )
    return {'ok': True, 'report_id': rid, 'report_type': 'effluent', 'totals': totals, 'streams': stream_reports}


def build_carbon_report(
    chemistry: BatchChemistry,
    batches_count: int,
    period_start: str,
    period_end: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    ensure_ehs_schema()
    carbon_per_batch = chemistry.carbon_kg()
    sox_per_batch = chemistry.sox_kg()
    results = {
        'process_mass_intensity': round(chemistry.process_mass_intensity(), 3),
        'carbon_kg_per_batch': round(carbon_per_batch, 3),
        'carbon_kg_total': round(carbon_per_batch * batches_count, 3),
        'carbon_kg_per_kg_product': round(carbon_per_batch / max(chemistry.batch_size_kg, 1.0), 3),
        'sox_kg_per_batch': round(sox_per_batch, 3),
        'sox_kg_total': round(sox_per_batch * batches_count, 3),
        'energy_kwh_per_batch': round(chemistry.energy_kwh, 3),
        'solvent_inputs_kg': chemistry.solvent_inputs_kg,
        'reagent_inputs_kg': chemistry.reagent_inputs_kg,
    }
    parameters = {
        'product_name': chemistry.product_name,
        'batch_size_kg': chemistry.batch_size_kg,
        'batches_count': batches_count,
        'period_start': period_start,
        'period_end': period_end,
        'chemistry': chemistry.__dict__,
    }
    rid = db.execute(
        'INSERT INTO ehs_reports(report_type, period_start, period_end, product_name, batch_size_kg, batches_count, parameters_json, results_json, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('carbon', period_start, period_end, chemistry.product_name, chemistry.batch_size_kg, batches_count, json.dumps(parameters), json.dumps(results), user_id),
    )
    return {'ok': True, 'report_id': rid, 'report_type': 'carbon', 'results': results}


def list_reports(report_type: str | None = None) -> list[dict[str, Any]]:
    ensure_ehs_schema()
    where = ['1=1']
    params: list[Any] = []
    if report_type:
        where.append('report_type=?')
        params.append(report_type)
    rows = db.query(f"SELECT * FROM ehs_reports WHERE {' AND '.join(where)} ORDER BY created_at DESC", tuple(params))
    out = []
    for r in rows:
        out.append({
            **dict(r),
            'parameters': _load_json(r.get('parameters_json'), {}),
            'results': _load_json(r.get('results_json'), {}),
        })
    return out


def get_report(report_id: int) -> dict[str, Any] | None:
    ensure_ehs_schema()
    row = db.one('SELECT * FROM ehs_reports WHERE id=?', (report_id,))
    if not row:
        return None
    return {
        **dict(row),
        'parameters': _load_json(row.get('parameters_json'), {}),
        'results': _load_json(row.get('results_json'), {}),
    }


def export_report_pdf(report_id: int) -> Path:
    from .document_engine import BrandedPDF, DocumentLine, DocumentSection, FormatConfig
    report = get_report(report_id)
    if not report:
        raise ValueError('Report not found')
    rtype = report.get('report_type', 'ehs')
    pdf = BrandedPDF(
        title=f'EHS Report — {rtype.replace("_", " ").title()}',
        doc_id=f'EHS-{rtype.upper()}-{report_id}-{_now().replace(":", "")}',
        kind='ehs',
        format_config=FormatConfig(footer_text='Generated by SHIMS EHS Engine'),
    )
    pdf.add_meta('Report ID', str(report_id))
    pdf.add_meta('Product', report.get('product_name') or '-')
    pdf.add_meta('Period', f"{report.get('period_start') or '-'} to {report.get('period_end') or '-'}")
    pdf.add_meta('Batches', str(report.get('batches_count') or '-'))
    results = report.get('results', {})
    if rtype == 'effluent':
        for idx, s in enumerate(results.get('streams', []), 1):
            pdf.add_section(DocumentSection(
                title=f'{idx}. Stream: {s.get("name", "-")}',
                order=idx * 10,
                lines=[
                    DocumentLine(key=f's_{idx}_vol', label='Volume per batch (L)', value=str(s.get('volume_liters_per_batch', '-')), type='text'),
                    DocumentLine(key=f's_{idx}_cod', label='COD (mg/L)', value=str(s.get('cod_mg_l', '-')), type='text'),
                    DocumentLine(key=f's_{idx}_bod', label='BOD (mg/L)', value=str(s.get('bod_mg_l', '-')), type='text'),
                    DocumentLine(key=f's_{idx}_tds', label='TDS (mg/L)', value=str(s.get('tds_mg_l', '-')), type='text'),
                    DocumentLine(key=f's_{idx}_ph', label='pH', value=s.get('ph_range', '-'), type='text'),
                ],
            ))
        totals = results.get('totals', {})
        pdf.add_section(DocumentSection(
            title='Totals',
            order=1000,
            lines=[DocumentLine(key='totals', label='Load Summary', value=json.dumps(totals, default=str), type='text')],
        ))
    elif rtype == 'carbon':
        pdf.add_section(DocumentSection(
            title='Carbon & SOx Summary',
            order=10,
            lines=[
                DocumentLine(key='pmi', label='Process Mass Intensity', value=str(results.get('process_mass_intensity', '-')), type='text'),
                DocumentLine(key='co2', label='CO2e kg/batch', value=str(results.get('carbon_kg_per_batch', '-')), type='text'),
                DocumentLine(key='co2_total', label='CO2e kg total', value=str(results.get('carbon_kg_total', '-')), type='text'),
                DocumentLine(key='sox', label='SOx kg/batch', value=str(results.get('sox_kg_per_batch', '-')), type='text'),
                DocumentLine(key='sox_total', label='SOx kg total', value=str(results.get('sox_kg_total', '-')), type='text'),
            ],
        ))
    path = GENERATED_DIR / f'ehs_report_{rtype}_{report_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    return pdf.build(path)


def default_effluent_streams_for_api() -> list[EffluentStream]:
    """Return a starter set of effluent streams common to API manufacturing."""
    return [
        EffluentStream(name='Reaction aqueous effluent', volume_liters_per_batch=500.0, cod_mg_l=15000.0, bod_mg_l=5000.0, tds_mg_l=30000.0, ph_min=5.0, ph_max=9.0),
        EffluentStream(name='Wash/extraction effluent', volume_liters_per_batch=800.0, cod_mg_l=8000.0, bod_mg_l=2500.0, tds_mg_l=15000.0, ph_min=6.0, ph_max=8.0),
        EffluentStream(name='Crystallization mother liquor', volume_liters_per_batch=300.0, cod_mg_l=25000.0, bod_mg_l=8000.0, tds_mg_l=50000.0, ph_min=4.0, ph_max=8.0),
        EffluentStream(name='Equipment cleaning CIP', volume_liters_per_batch=1200.0, cod_mg_l=2000.0, bod_mg_l=600.0, tds_mg_l=5000.0, ph_min=6.5, ph_max=8.5),
    ]
