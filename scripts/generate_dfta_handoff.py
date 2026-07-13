"""Generate DFTA Plant-Ready Sprint handoff PDF."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.document_engine import BrandedPDF, DocumentSection, DocumentLine, FormatConfig

content = """
# SHIMS Enterprise — DFTA Plant-Ready Sprint Handoff
Date: 2026-06-09
Sprint goal: Unblock DFTA experimentation and plant transfer in 3 days.

## What was broken (root causes fixed)

1. rd_lead role missing from ROLE_ALLOWED and ROLE_OPTIONS
   → File: shims_enterprise/core.py
   → Added rd_lead with same module access as rd + lims/dms/rim.

2. Experiment finalization never updated status
   → File: shims_enterprise/app.py /api/rd/v2/experiments/finalize
   → Now calls update_rd_experiment(..., {'status': 'finalized'}).

3. Save-as-template never updated source status
   → File: shims_enterprise/app.py /api/rd/v2/experiments/{id}/save-as-template
   → Now sets rd_experiments.status = 'template'.

4. AI Lab tab hidden
   → File: shims_enterprise/app.py /rd/process route
   → Now passes ai_lab_access = 'ailab' in rd_tab_access to template.

5. BMR entry point hidden
   → File: shims_enterprise/templates/bmr_list.html
   → Added "+ Generate BMR" button linking to production planning.

## SOP Builder UX improvements

- Seeded "GMP SOP (Default)" template with 14 standard sections.
- Added "Start from template" picker in SOP Builder.
- Added auto-save to localStorage with restore on load.
- Added live preview toggle (debounced 900 ms).
- Simplified section editor: collapsible cards, preset title dropdown, word count.
- Added proposed doc number field using /api/documents/proposed-doc-no.
- New endpoint: GET /api/documents/proposed-doc-no?sop=1&department=QA.

## DFTA rapid plant-ready workflow

- Seeded DFTA-specific R&D template and production defaults.
  → DFTA_STAGE_DEFAULTS (3 stages: alkylation, purification, isolation/drying)
  → MATERIAL_REQUIREMENTS['dfta']
  → seed_dfta_rd_template()
- New backend helpers in shared/equipment_intelligence.py:
  → create_tech_transfer_package_from_experiment(user_id, experiment_id, data)
  → rapid_plant_readiness(plan_id, product_name, target_qty, desired_start_date)
- New endpoints:
  → POST /api/rd/tech-transfer/from-experiment
  → GET /api/production/feasibility/rapid?plan_id=... or &product_name=...&target_qty=...
  → POST /api/rd/dfta/plant-ready
    Body: {experiment_id, target_qty, target_batch_size, desired_start_date}
    Orchestrates: tech transfer → scale-up trial → production plan → BMR → MES sync → feasibility check.

## UI shortcuts added

- /rd/process: "Load DFTA Template" button next to Fluconazole.
- /rd/process experiment detail: Create Tech Transfer, Check 3-Day Readiness, Plant Ready.
- /production/planning: 3-Day Check button + auto-fetched readiness badge per plan.

## Test status

- 23 selected tests pass: test_app.py, test_smoke.py, test_v15_tool_contracts.py, test_document_engine.py.

## Next recommended steps for follow-up sessions

1. Run enterprise server and log in as rd_lead.
2. Open /rd/process, click "Load DFTA Template", create experiment.
3. Add a stage and raw materials, then finalize.
4. Verify status changes to "finalized" and experiment appears in BMR generation.
5. Click "Plant Ready" on the finalized experiment to run full orchestration.
6. Open /production/planning to verify plan, BMR, and readiness badge.
7. Open /documents, switch to SOP Builder, select GMP SOP template, edit sections, toggle live preview.
"""

sections = []
for idx, para in enumerate(content.strip().split('\n\n'), 1):
    lines = para.split('\n')
    title = lines[0].lstrip('#').strip()
    body = '\n'.join(lines[1:]).strip()
    sections.append(DocumentSection(
        title=f'{idx}. {title}',
        order=idx * 10,
        lines=[DocumentLine(key=f'sec_{idx}', label='Text', value=body, type='text', required=True)],
    ))

config = FormatConfig(header_font_size=16, body_font_size=10, watermark_text='', footer_text='SHIMS Enterprise DFTA Sprint Handoff')
pdf = BrandedPDF(title='SHIMS Enterprise — DFTA Plant-Ready Sprint Handoff', doc_id='DFTA-HANDOFF-2026-001', kind='report', format_config=config)
pdf.add_meta('Sprint', 'DFTA Plant-Ready 3-Day')
pdf.add_meta('Date', '2026-06-09')
pdf.add_meta('Test Status', '23/23 passed')
for sec in sections:
    pdf.add_section(sec)

out = Path('generated/SHIMS_DFTA_Plant_Ready_Sprint_Handoff.pdf')
out.parent.mkdir(parents=True, exist_ok=True)
pdf.build(out)
print(f'Generated {out}')
