"""BMR (Batch Manufacturing Record) PDF Generator for SHIMS Enterprise.

Generates GxP-compliant BMR PDFs with support for coded raw materials.
Two versions:
  - Coded BMR: plant operators see code names only
  - Decoded BMR: lead chemist sees real names + code names
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from .config import GENERATED_DIR
from .document_engine import BrandedPDF, DocumentLine, DocumentSection, FormatConfig
from .database import db
from .enterprise_pharma_core import (
    get_bmr_record, get_bmr_stages, get_bmr_steps,
    get_coded_materials, get_rd_experiment_detail,
    resolve_material_name, resolve_text,
)


class BMRPDFGenerator:
    """Generate branded BMR PDF documents with optional material coding."""

    def __init__(self, bmr_id: int, user_role: str = 'production'):
        self.bmr_id = bmr_id
        self.user_role = user_role
        self.bmr = get_bmr_record(bmr_id)
        if not self.bmr:
            raise ValueError(f'BMR {bmr_id} not found')
        self.experiment_id = self.bmr.get('experiment_id')
        self.stages = get_bmr_stages(bmr_id)
        self.steps = get_bmr_steps(bmr_id)
        self.coded_materials = get_coded_materials(self.experiment_id) if self.experiment_id else []
        self.is_decoded = user_role in {'rd_lead', 'admin', 'executive'}
        self.encoding_label = 'DECODED' if self.is_decoded else 'CODED'

    def _resolve(self, text: str) -> str:
        if self.is_decoded or not self.experiment_id:
            return text or ''
        return resolve_text(self.experiment_id, text, self.user_role)

    def _resolve_name(self, name: str) -> str:
        if self.is_decoded or not self.experiment_id:
            return name or ''
        return resolve_material_name(self.experiment_id, name, self.user_role)

    def _watermark(self) -> str:
        if self.is_decoded:
            return 'CONFIDENTIAL — LEAD ONLY'
        return 'PLANT COPY — CODED MATERIALS'

    def build(self) -> Path:
        """Build the PDF and return its path."""
        pdf = BrandedPDF(
            title=f"Batch Manufacturing Record — {self.bmr['product_name']}",
            doc_id=self.bmr.get('bmr_no', f'BMR-{self.bmr_id}'),
            kind='bmr',
            format_config=FormatConfig(
                header_font_size=14,
                body_font_size=9,
                table_header_bg='#1E3A5F',
                primary_color='#0F172A',
                accent_color='#2563EB',
                watermark_text=self._watermark(),
                watermark_color='#E2E8F0' if not self.is_decoded else '#FECACA',
                show_logo=True,
                signature_lines=3,
                footer_text=f"BMR {self.encoding_label} | {self.bmr.get('batch_no', '')}",
            ),
        )

        pdf.add_meta('Product', self.bmr.get('product_name', ''))
        pdf.add_meta('Batch No', self.bmr.get('batch_no', ''))
        pdf.add_meta('BMR No', self.bmr.get('bmr_no', ''))
        pdf.add_meta('Encoding', self.encoding_label)

        # Section 1: Header
        pdf.add_section(self._section_header())
        # Section 2: Approval signatures
        pdf.add_section(self._section_signatures())
        # Section 3: Materials BOM
        pdf.add_section(self._section_materials())
        # Section 4: Equipment list
        pdf.add_section(self._section_equipment())
        # Section 5+: Stage execution (one section per stage)
        for stage in self.stages:
            pdf.add_section(self._section_stage(stage))
        # Section: QC results
        pdf.add_section(self._section_qc())
        # Section: Yield summary
        pdf.add_section(self._section_yield())
        # Section: Deviations
        pdf.add_section(self._section_deviations())
        # Section: Batch release checklist
        pdf.add_section(self._section_release_checklist())
        # Section: Decoding sheet (decoded version only)
        if self.is_decoded and self.coded_materials:
            pdf.add_section(self._section_decoding_sheet())

        filename = f"BMR_{self.bmr.get('batch_no', 'BATCH')}_{self.bmr.get('bmr_no', self.bmr_id)}_{self.encoding_label}.pdf"
        output_path = GENERATED_DIR / filename
        return pdf.build(output_path)

    def _section_header(self) -> DocumentSection:
        lines = [
            DocumentLine(key='product', label='Product Name', value=self.bmr.get('product_name', ''), type='text'),
            DocumentLine(key='batch', label='Batch No', value=self.bmr.get('batch_no', ''), type='text'),
            DocumentLine(key='bmr_no', label='BMR No', value=self.bmr.get('bmr_no', ''), type='text'),
            DocumentLine(key='target_qty', label='Target Quantity', value=f"{self.bmr.get('target_qty') or ''} {self.bmr.get('unit', 'kg')}", type='text'),
            DocumentLine(key='encoding', label='Encoding', value=self.encoding_label, type='text'),
            DocumentLine(key='status', label='BMR Status', value=(self.bmr.get('status') or 'draft').upper(), type='text'),
        ]
        if self.bmr.get('generated_at'):
            lines.append(DocumentLine(key='generated', label='Generated', value=self.bmr['generated_at'], type='text'))
        return DocumentSection(title='1. BATCH IDENTIFICATION', lines=lines, order=1)

    def _section_signatures(self) -> DocumentSection:
        lines = [
            DocumentLine(key='prepared', label='Prepared By (Production)', value='_____________________', type='signature'),
            DocumentLine(key='prepared_date', label='Date', value='_______________', type='text'),
            DocumentLine(key='reviewed', label='Reviewed By (QA)', value='_____________________', type='signature'),
            DocumentLine(key='reviewed_date', label='Date', value='_______________', type='text'),
            DocumentLine(key='approved', label='Approved By (Plant Head)', value='_____________________', type='signature'),
            DocumentLine(key='approved_date', label='Date', value='_______________', type='text'),
        ]
        return DocumentSection(title='2. APPROVAL SIGNATURES', lines=lines, order=2)

    def _section_materials(self) -> DocumentSection:
        lines = [DocumentLine(key='mat_header', label='Material', value='Qty / Unit', type='subheader')]
        seen = set()
        # Collect materials from all stages
        for stage in self.stages:
            rm_desc = stage.get('rm_description_coded') if not self.is_decoded else stage.get('rm_description')
            if not rm_desc:
                continue
            # Parse simple "name (qty unit)" or "name qty unit" patterns
            parts = (rm_desc or '').split(',')
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                # Try to extract name and quantity
                match = self._parse_material_part(part)
                if match:
                    name, qty, unit = match
                    key = f"{name.lower()}|{qty}|{unit}"
                    if key not in seen:
                        seen.add(key)
                        display_name = self._resolve_name(name) if not self.is_decoded else name
                        lines.append(DocumentLine(
                            key=f'mat_{len(seen)}',
                            label=display_name,
                            value=f'{qty} {unit}',
                            type='text',
                        ))
        if len(lines) == 1:
            lines.append(DocumentLine(key='no_mat', label='No materials recorded', value='', type='text'))
        return DocumentSection(title='3. BILL OF MATERIALS', lines=lines, order=3)

    def _parse_material_part(self, part: str):
        """Parse a material description part like '2,4-Difluorobenzyl bromide (100g)' or 'K2CO3 55 g'."""
        import re
        # Pattern: name (qty unit)
        m = re.match(r'^(.+?)\s*\(\s*([0-9.]+)\s*([a-zA-Z%]+)\s*\)\s*$', part)
        if m:
            return m.group(1).strip(), m.group(2), m.group(3)
        # Pattern: name qty unit
        m = re.match(r'^(.+?)\s+([0-9.]+)\s*([a-zA-Z%]+)\s*$', part)
        if m:
            return m.group(1).strip(), m.group(2), m.group(3)
        # Fallback: just name
        return part, '', ''

    def _section_equipment(self) -> DocumentSection:
        lines = [DocumentLine(key='eq_header', label='Equipment Code', value='Stage', type='subheader')]
        seen = set()
        for stage in self.stages:
            eq = stage.get('equipment_code')
            if eq and eq not in seen:
                seen.add(eq)
                lines.append(DocumentLine(
                    key=f'eq_{eq}',
                    label=eq,
                    value=stage.get('stage_name', ''),
                    type='text',
                ))
        if len(lines) == 1:
            lines.append(DocumentLine(key='no_eq', label='No equipment assigned', value='', type='text'))
        return DocumentSection(title='4. EQUIPMENT LIST', lines=lines, order=4)

    def _section_stage(self, stage: dict[str, Any]) -> DocumentSection:
        stage_no = stage.get('stage_no', 1)
        stage_name = stage.get('stage_name', f'Stage {stage_no}')
        
        rm_desc = stage.get('rm_description_coded') if not self.is_decoded else stage.get('rm_description')
        if rm_desc:
            rm_desc = self._resolve(rm_desc)
        
        lines = [
            DocumentLine(key=f's{stage_no}_name', label='Stage Name', value=stage_name, type='text', bold=True),
            DocumentLine(key=f's{stage_no}_rm', label='Raw Material(s)', value=rm_desc or '—', type='text'),
            DocumentLine(key=f's{stage_no}_solvent', label='Solvent', value=self._resolve(stage.get('solvent', '')), type='text'),
            DocumentLine(key=f's{stage_no}_catalyst', label='Catalyst', value=self._resolve(stage.get('catalyst', '')), type='text'),
            DocumentLine(key=f's{stage_no}_temp', label='Temperature', value=f"{stage.get('temperature_c') or '—'} °C", type='text'),
            DocumentLine(key=f's{stage_no}_ph', label='pH', value=f"{stage.get('ph_value') or '—'}", type='text'),
            DocumentLine(key=f's{stage_no}_pressure', label='Pressure', value=f"{stage.get('pressure_bar') or '—'} bar", type='text'),
            DocumentLine(key=f's{stage_no}_time', label='Reaction Time', value=f"{stage.get('reaction_time_minutes') or '—'} min", type='text'),
            DocumentLine(key=f's{stage_no}_yield', label='Expected Yield', value=f"{stage.get('expected_yield_pct') or '—'} %", type='text'),
            DocumentLine(key=f's{stage_no}_actual_yield', label='Actual Yield', value=f"{stage.get('actual_yield_pct') or '—'} %", type='text'),
            DocumentLine(key=f's{stage_no}_purity', label='Purity', value=f"{stage.get('purity_pct') or '—'} %", type='text'),
            DocumentLine(key=f's{stage_no}_operator', label='Operator Sign', value='_____________________', type='signature'),
            DocumentLine(key=f's{stage_no}_qa', label='QA Review', value='_____________________', type='signature'),
        ]
        
        # Add steps for this stage
        stage_steps = [s for s in self.steps if s.get('bmr_stage_id') == stage.get('id')]
        if stage_steps:
            lines.append(DocumentLine(key=f's{stage_no}_steps', label='Execution Steps', value='', type='subheader'))
            for i, step in enumerate(stage_steps, 1):
                mat = step.get('material_name_coded') if not self.is_decoded else step.get('material_name')
                if mat:
                    mat = self._resolve(mat)
                step_text = f"{i}. {step.get('step_name', 'Step')}: Expected={step.get('expected_value') or '—'}, Actual={step.get('actual_value') or '—'}"
                if mat:
                    step_text += f", Material={mat}"
                lines.append(DocumentLine(
                    key=f's{stage_no}_step_{i}',
                    label='',
                    value=step_text,
                    type='text',
                    indent=1,
                ))
        
        return DocumentSection(title=f'5.{stage_no} STAGE EXECUTION — {stage_name}', lines=lines, order=50 + stage_no)

    def _section_qc(self) -> DocumentSection:
        lines = [
            DocumentLine(key='qc_header', label='Test', value='Result / Spec', type='subheader'),
        ]
        has_qc = False
        if self.experiment_id:
            tests = db.query(
                'SELECT test_name, method_ref, specification, result_value, result_unit, pass_fail, stage_id FROM rd_stage_tests WHERE experiment_id=? ORDER BY stage_id, test_name',
                (self.experiment_id,),
            )
            impurities = db.query(
                'SELECT impurity_name, value_pct, impurity_type, stage_id FROM rd_impurity_profiles WHERE experiment_id=? ORDER BY stage_id, impurity_name',
                (self.experiment_id,),
            )
            stage_names = {s['id']: s['stage_name'] for s in self.stages}
            for t in tests:
                has_qc = True
                spec = f"{t.get('specification') or ''} {t.get('result_unit') or ''}".strip()
                result = f"{t.get('result_value') or '-'} {t.get('result_unit') or ''} ({t.get('pass_fail') or 'pending'})".strip()
                lines.append(DocumentLine(
                    key=f"qc_test_{t.get('stage_id')}_{t.get('test_name')}",
                    label=f"{stage_names.get(t.get('stage_id'), 'Stage')} — {t.get('test_name')}",
                    value=result,
                    spec=spec,
                    type='text',
                ))
            for imp in impurities:
                has_qc = True
                lines.append(DocumentLine(
                    key=f"qc_imp_{imp.get('stage_id')}_{imp.get('impurity_name')}",
                    label=f"{stage_names.get(imp.get('stage_id'), 'Stage')} — Impurity {imp.get('impurity_name')}",
                    value=f"{imp.get('value_pct') or '-'} %",
                    spec=f"Type: {imp.get('impurity_type')}",
                    type='text',
                ))
        if not has_qc:
            lines.append(DocumentLine(key='qc_placeholder', label='No QC results recorded', value='Log in-process tests and impurities in the R&D module.', type='text'))
        return DocumentSection(title='6. IN-PROCESS QC RESULTS', lines=lines, order=60)

    def _section_yield(self) -> DocumentSection:
        total_expected = 0.0
        total_actual = 0.0
        count = 0
        for stage in self.stages:
            if stage.get('expected_yield_pct'):
                total_expected += float(stage['expected_yield_pct'])
                count += 1
            if stage.get('actual_yield_pct'):
                total_actual += float(stage['actual_yield_pct'])
        avg_expected = round(total_expected / count, 2) if count else 0
        avg_actual = round(total_actual / count, 2) if count else 0
        
        lines = [
            DocumentLine(key='yield_expected', label='Average Expected Yield', value=f'{avg_expected} %', type='text'),
            DocumentLine(key='yield_actual', label='Average Actual Yield', value=f'{avg_actual} %', type='text'),
            DocumentLine(key='yield_variance', label='Variance', value=f'{round(avg_actual - avg_expected, 2)} %', type='text'),
        ]
        return DocumentSection(title='7. YIELD SUMMARY', lines=lines, order=70)

    def _section_deviations(self) -> DocumentSection:
        lines = [
            DocumentLine(key='dev_header', label='Deviation / Exception', value='Resolution', type='subheader'),
        ]
        has_deviation = False
        for step in self.steps:
            if step.get('exception_notes'):
                has_deviation = True
                lines.append(DocumentLine(
                    key=f'dev_{step.get("id")}',
                    label=step.get('step_name', ''),
                    value=step.get('exception_notes', ''),
                    type='text',
                ))
        if not has_deviation:
            lines.append(DocumentLine(key='no_dev', label='No deviations recorded', value='', type='text'))
        return DocumentSection(title='8. DEVIATIONS & EXCEPTIONS', lines=lines, order=80)

    def _section_release_checklist(self) -> DocumentSection:
        lines = [
            DocumentLine(key='rl_qc', label='QC testing completed and passed', value='☐ Yes  ☐ No', type='text'),
            DocumentLine(key='rl_bmr', label='BMR completed and reviewed', value='☐ Yes  ☐ No', type='text'),
            DocumentLine(key='rl_dev', label='All deviations closed', value='☐ Yes  ☐ No', type='text'),
            DocumentLine(key='rl_eq', label='Equipment cleaning verified', value='☐ Yes  ☐ No', type='text'),
            DocumentLine(key='rl_mat', label='Material reconciliation complete', value='☐ Yes  ☐ No', type='text'),
            DocumentLine(key='rl_sign', label='QA Release Sign', value='_____________________', type='signature'),
        ]
        return DocumentSection(title='9. BATCH RELEASE CHECKLIST', lines=lines, order=90)

    def _section_decoding_sheet(self) -> DocumentSection:
        lines = [
            DocumentLine(key='decode_warn', label='WARNING', value='This sheet is confidential. Do not distribute to production or QC.', type='text', color='#DC2626', bold=True),
            DocumentLine(key='decode_header', label='Real Name', value='Code Name | Type | Key RM', type='subheader'),
        ]
        for code in self.coded_materials:
            lines.append(DocumentLine(
                key=f'code_{code.get("id")}',
                label=code.get('real_name', ''),
                value=f"{code.get('code_name', '')} | {code.get('material_type', '')} | {'Yes' if code.get('is_key_rm') else 'No'}",
                type='text',
            ))
        return DocumentSection(title='ANNEXURE: CONFIDENTIAL DECODING SHEET', lines=lines, order=100, page_break_before=True)
