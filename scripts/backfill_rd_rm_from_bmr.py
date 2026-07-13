"""Back-fill raw materials/solvents for existing R&D experiments that have none.

Matches experiments by product_name to the best BMR summary, parses the raw-material
section, and seeds experiment-level materials.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.database import db
from shared.rd_lab import add_experiment_raw_material, add_solvent
from shared.bmr_raw_material_parser import (
    fetch_best_bmr_summary,
    parse_raw_materials_from_summary,
    parse_solvents_from_summary,
)


def backfill() -> dict[str, int]:
    empty_exps = db.query(
        """
        SELECT e.id, e.product_name
        FROM rd_experiments e
        LEFT JOIN rd_experiment_raw_materials r ON r.experiment_id = e.id
        GROUP BY e.id
        HAVING COUNT(r.id) = 0
        ORDER BY e.id DESC
        """
    )
    filled = 0
    skipped = 0
    for exp in empty_exps:
        exp_id = exp['id']
        product = exp['product_name']
        summary = fetch_best_bmr_summary(product)
        if not summary:
            skipped += 1
            continue
        rms = parse_raw_materials_from_summary(summary)
        solvents = parse_solvents_from_summary(summary)
        if not rms and not solvents:
            skipped += 1
            continue
        for r in rms[:20]:
            add_experiment_raw_material(None, exp_id, {
                'name': r['name'],
                'quantity': r['quantity'] if r['quantity'] is not None else 0,
                'unit': r['unit'],
                'unit_type': r['unit_type'],
                'notes': 'Back-filled from BMR raw-material table',
            }, audit=False)
        for s in solvents[:10]:
            qty_ml = s['quantity']
            if s['unit'].lower() == 'l' and qty_ml is not None:
                qty_ml *= 1000.0
            add_solvent(None, exp_id, {
                'name': s['name'],
                'quantity_ml': qty_ml if qty_ml is not None else 0,
                'notes': 'Back-filled from BMR raw-material table',
            }, audit=False)
        db.execute(
            "UPDATE rd_experiments SET notes = COALESCE(notes,'') || ' [RMs back-filled from BMR]' WHERE id=?",
            (exp_id,),
        )
        filled += 1
    return {'filled': filled, 'skipped': skipped, 'total_empty': len(empty_exps)}


if __name__ == '__main__':
    result = backfill()
    print(result)
