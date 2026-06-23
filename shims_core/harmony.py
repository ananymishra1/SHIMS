from __future__ import annotations

from sqlalchemy.orm import Session
from .models import Experiment, COARecord, InventoryItem, ProductionBatch, ProcurementRequest
from .pharma_ai import warehouse_alerts


def overview(db: Session):
    experiments = db.query(Experiment).order_by(Experiment.created_at.desc()).limit(20).all()
    coas = db.query(COARecord).order_by(COARecord.created_at.desc()).limit(20).all()
    items = db.query(InventoryItem).order_by(InventoryItem.material_name).all()
    batches = db.query(ProductionBatch).order_by(ProductionBatch.created_at.desc()).limit(20).all()
    procurement = db.query(ProcurementRequest).order_by(ProcurementRequest.created_at.desc()).limit(20).all()
    low_items = [i for i in items if (i.stock_qty or 0) <= (i.reorder_level or 0)]
    return {
        'counts': {
            'experiments': len(experiments),
            'coa_records': len(coas),
            'inventory_items': len(items),
            'production_batches': len(batches),
            'procurement_requests': len(procurement),
        },
        'active_experiments': [e.to_dict() for e in experiments if e.status in ['planned', 'running']],
        'pending_coas': [c.to_dict() for c in coas if c.status in ['draft', 'review', 'pending']],
        'low_stock': [i.to_dict() for i in low_items],
        'blocked_batches': [b.to_dict() for b in batches if b.blockers],
        'open_procurement': [p.to_dict() for p in procurement if p.status not in ['closed', 'rejected']],
        'alerts': warehouse_alerts([i.to_dict() for i in low_items]),
    }
