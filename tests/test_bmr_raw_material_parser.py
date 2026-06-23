import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.bmr_raw_material_parser import (
    extract_route_stages_from_text,
    parse_raw_materials_from_summary,
    parse_solvents_from_summary,
)


_MINOXIDIL_SUMMARY = """
RAW MATERIALS:

1.  2,4 - Di amino - 6 - chloropyrimidine  Kg 100
2.  Sodium tungstate  Kg 13.2
3.  H2O2  Kg 133.2
4.  Methanol  Ltr 470
5.  Ethanolamine  Kg 700
6.  DM water  Ltr 4000
"""

_DFTA_CONSUMPTION = """
RAW MATERIAL CONSUMPTION (Per Batch)
S.No Name of Chemicals Qty (Kg) Rate (Rs.) Amount (Rs.)
1 1,3 DFB Kg 576.88 1260.00 726862.50
2 2,6 DCP Kg 456.76 580.00 264920.80
3 MDC Kg 371.05 65.00 24118.25
4 IPA Ltr 2495.60 85.00 212126.00
5 Charcoal Kg 30.00 170.00 5100.00
6 Methanol Ltr 220.00 35.00 7700.00
"""

_DFTA_COSTING = """
S.No | Raw Material | Qty | UOM
1 | MDC | 371 | Kg
2 | 1,3 DFB | 577 | Kg
3 | 2,6 DCP | 457 | Kg
4 | IPA | 2500 | Ltr
"""


def test_parse_numbered_minoxidil():
    rms = parse_raw_materials_from_summary(_MINOXIDIL_SUMMARY)
    names = {r['name'] for r in rms}
    assert '2,4 - Di amino - 6 - chloropyrimidine' in names
    assert 'Sodium tungstate' in names
    assert 'H2O2' in names
    assert any(r['name'] == 'Methanol' and r['unit'] == 'L' and r['quantity'] == 470.0 for r in rms)


def test_solvent_detection_minoxidil():
    sols = parse_solvents_from_summary(_MINOXIDIL_SUMMARY)
    names = {s['name'] for s in sols}
    assert 'Methanol' in names
    assert 'Ethanolamine' not in names


def test_parse_dfta_consumption():
    rms = parse_raw_materials_from_summary(_DFTA_CONSUMPTION)
    by_name = {r['name']: r for r in rms}
    assert by_name['1,3 DFB']['quantity'] == 576.88
    assert by_name['MDC']['quantity'] == 371.05
    assert by_name['IPA']['unit'] == 'L'


def test_parse_dfta_costing_pipe_table():
    rms = parse_raw_materials_from_summary(_DFTA_COSTING)
    by_name = {r['name']: r for r in rms}
    assert by_name['MDC']['quantity'] == 371.0
    assert by_name['1,3 DFB']['quantity'] == 577.0
    assert by_name['IPA']['unit'] == 'L'


def test_chemical_formula_numbers_not_confused_with_qty():
    # H2O2 contains the digit "2"; it must not be picked as the quantity.
    rms = parse_raw_materials_from_summary(_MINOXIDIL_SUMMARY)
    h2o2 = next(r for r in rms if r['name'] == 'H2O2')
    assert h2o2['quantity'] == 133.2


_VILADAGLIPTIN_PROCESS = """
BRIEF PROCESS (VILDAGLIPTIN)
Stage I : L-Prolinamide reacts with chloro acetyl chloride and potassium carbonate in presence of
acetonitrile as a solvent media followed by purification using ethyl acetate to give Stage-I as a product.
Stage II : Stage-I reacts with phosphorus oxychloride in presence of ethyl acetate as a solvent media
followed by purification using isopropyl alcohol and methyl tertary butyl ether to give Stage-II as a product.
"""


def test_extract_materials_from_narrative_process():
    rms = parse_raw_materials_from_summary(_VILADAGLIPTIN_PROCESS)
    names = {r['name'] for r in rms}
    assert 'L-Prolinamide' in names
    assert 'chloro acetyl chloride' in names
    assert 'potassium carbonate' in names
    assert 'phosphorus oxychloride' in names


def test_extract_stages_from_narrative_process():
    stages = extract_route_stages_from_text(_VILADAGLIPTIN_PROCESS)
    assert len(stages) == 2
    assert any('L-Prolinamide' in [m.get('name') if isinstance(m, dict) else m for m in s['raw_materials']] for s in stages)
    assert any('phosphorus oxychloride' in [m.get('name') if isinstance(m, dict) else m for m in s['raw_materials']] for s in stages)


_SINGLE_STAGE_BMR_TABLE = """
BATCH MANUFACTURING RECORD
Product Name: Purification of DFTA

RAW MATERIAL:
1. DFTA Kg 50
2. DM Water L 200
3. HCl L 10

EQUIPMENT:
Charge the material in a glass lined reactor.
Maintain 5-10 deg C for 1 h.
"""


def test_single_stage_bmr_table_feeds_stage_materials():
    rms = parse_raw_materials_from_summary(_SINGLE_STAGE_BMR_TABLE)
    by_name = {r['name']: r for r in rms}
    assert by_name['DM Water']['unit'] == 'L'
    assert by_name['DM Water']['unit_type'] == 'volume'

    stages = extract_route_stages_from_text(_SINGLE_STAGE_BMR_TABLE)
    assert len(stages) == 1
    assert 'DFTA' in [m.get('name') if isinstance(m, dict) else m for m in stages[0]['raw_materials']]
    assert 'DM Water' in [m.get('name') if isinstance(m, dict) else m for m in stages[0]['solvents']]
    assert stages[0]['conditions']['temperature_c_min'] == 5.0
    assert stages[0]['conditions']['temperature_c_max'] == 10.0
    assert 'Glass Lined Reactor' in stages[0]['equipment']
