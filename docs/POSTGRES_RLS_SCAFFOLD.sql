-- SHIMS PostgreSQL row-level-security scaffold for later production hardening.
-- This is not used by the local SQLite pilot.

ALTER TABLE experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE coa_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE coa_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_movements ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE procurement_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- Example: bind app.department and app.role per request/session.
-- Admin/executive can see all; department users see only their own domain.
CREATE POLICY experiments_department_policy ON experiments
  USING (current_setting('app.role', true) IN ('admin','executive','rd'));

CREATE POLICY coa_department_policy ON coa_records
  USING (current_setting('app.role', true) IN ('admin','executive','qc'));

CREATE POLICY inventory_department_policy ON inventory_items
  USING (current_setting('app.role', true) IN ('admin','executive','warehouse','procurement','production'));

CREATE POLICY production_department_policy ON production_batches
  USING (current_setting('app.role', true) IN ('admin','executive','production','qc','warehouse'));

CREATE POLICY procurement_department_policy ON procurement_requests
  USING (current_setting('app.role', true) IN ('admin','executive','procurement','warehouse'));
