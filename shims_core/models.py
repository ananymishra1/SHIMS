from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def now():
    return datetime.utcnow()


class DictMixin:
    def to_dict(self):
        out = {}
        for c in self.__table__.columns:
            v = getattr(self, c.name)
            if isinstance(v, datetime):
                v = v.isoformat()
            out[c.name] = v
        return out


class User(Base, DictMixin):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, index=True, nullable=False)
    display_name = Column(String(160), default='')
    password_hash = Column(String(300), nullable=False)
    role = Column(String(40), default='user')  # admin, executive, user
    department = Column(String(40), default='executive')
    site = Column(String(80), default='ujjain')
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)


class AuditLog(Base, DictMixin):
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True)
    actor = Column(String(120), default='system', index=True)
    action = Column(String(120), index=True)
    entity = Column(String(120), index=True)
    entity_id = Column(String(80), default='')
    details = Column(Text, default='{}')
    created_at = Column(DateTime, default=now)


class Experiment(Base, DictMixin):
    __tablename__ = 'experiments'
    id = Column(Integer, primary_key=True)
    product_name = Column(String(200), index=True)
    title = Column(String(240))
    objective = Column(Text, default='')
    hypothesis = Column(Text, default='')
    parameters_json = Column(Text, default='{}')
    status = Column(String(50), default='planned')
    stage = Column(String(80), default='lab')
    observations = Column(Text, default='')
    result_summary = Column(Text, default='')
    ai_suggestion = Column(Text, default='')
    owner = Column(String(120), default='')
    site = Column(String(80), default='ujjain')
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)


class COATemplate(Base, DictMixin):
    __tablename__ = 'coa_templates'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), index=True)
    product_name = Column(String(200), default='General')
    version = Column(String(40), default='1.0')
    schema_json = Column(Text, default='{"fields":[]}')
    is_active = Column(Boolean, default=True)
    created_by = Column(String(120), default='system')
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)


class COARecord(Base, DictMixin):
    __tablename__ = 'coa_records'
    id = Column(Integer, primary_key=True)
    template_id = Column(Integer, ForeignKey('coa_templates.id'))
    product_name = Column(String(200), index=True)
    batch_no = Column(String(120), index=True)
    sample_id = Column(String(120), default='')
    results_json = Column(Text, default='{}')
    status = Column(String(50), default='draft')
    prepared_by = Column(String(120), default='')
    approved_by = Column(String(120), default='')
    generated_file = Column(Text, default='')
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)
    template = relationship('COATemplate')


class Vendor(Base, DictMixin):
    __tablename__ = 'vendors'
    id = Column(Integer, primary_key=True)
    name = Column(String(200), index=True)
    gst = Column(String(80), default='')
    contact = Column(String(160), default='')
    phone = Column(String(80), default='')
    email = Column(String(160), default='')
    status = Column(String(50), default='pending')
    notes = Column(Text, default='')
    created_at = Column(DateTime, default=now)


class InventoryItem(Base, DictMixin):
    __tablename__ = 'inventory_items'
    id = Column(Integer, primary_key=True)
    material_code = Column(String(100), unique=True, index=True)
    material_name = Column(String(200), index=True)
    category = Column(String(100), default='raw material')
    stock_qty = Column(Float, default=0.0)
    uom = Column(String(40), default='kg')
    reorder_level = Column(Float, default=0.0)
    location = Column(String(120), default='main warehouse')
    vendor_id = Column(Integer, ForeignKey('vendors.id'), nullable=True)
    expiry_date = Column(String(40), default='')
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)
    vendor = relationship('Vendor')


class InventoryMovement(Base, DictMixin):
    __tablename__ = 'inventory_movements'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('inventory_items.id'))
    movement_type = Column(String(30))  # entry, exit, adjustment
    quantity = Column(Float, default=0.0)
    reference = Column(String(200), default='')
    note = Column(Text, default='')
    created_by = Column(String(120), default='')
    created_at = Column(DateTime, default=now)
    item = relationship('InventoryItem')


class ProductionBatch(Base, DictMixin):
    __tablename__ = 'production_batches'
    id = Column(Integer, primary_key=True)
    batch_no = Column(String(120), unique=True, index=True)
    product_name = Column(String(200), index=True)
    planned_qty = Column(Float, default=0.0)
    actual_qty = Column(Float, default=0.0)
    uom = Column(String(40), default='kg')
    status = Column(String(50), default='planned')
    stage = Column(String(80), default='planning')
    qc_status = Column(String(80), default='pending')
    blockers = Column(Text, default='')
    coa_record_id = Column(Integer, ForeignKey('coa_records.id'), nullable=True)
    created_by = Column(String(120), default='')
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)


class ProcurementRequest(Base, DictMixin):
    __tablename__ = 'procurement_requests'
    id = Column(Integer, primary_key=True)
    material_name = Column(String(200), index=True)
    quantity = Column(Float, default=0.0)
    uom = Column(String(40), default='kg')
    reason = Column(Text, default='')
    priority = Column(String(50), default='normal')
    status = Column(String(50), default='requested')
    requested_by = Column(String(120), default='')
    vendor_suggestion = Column(Text, default='')
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now)


class ChatMessage(Base, DictMixin):
    __tablename__ = 'omni_chat_messages'
    id = Column(Integer, primary_key=True)
    session_id = Column(String(120), default='default', index=True)
    role = Column(String(40), default='user')
    content = Column(Text, default='')
    meta_json = Column(Text, default='{}')
    created_at = Column(DateTime, default=now)


class GeneratedAsset(Base, DictMixin):
    __tablename__ = 'omni_generated_assets'
    id = Column(Integer, primary_key=True)
    asset_type = Column(String(60), index=True)
    prompt = Column(Text, default='')
    path = Column(Text, default='')
    provider = Column(String(80), default='local')
    created_at = Column(DateTime, default=now)
