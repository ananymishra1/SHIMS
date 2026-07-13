-- Optional PostgreSQL hardening scaffold after migration from SQLite.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE coa_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE coa_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_movements ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE production_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE procurement_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Tune these policies after final role mapping.
CREATE POLICY users_site_policy ON users
  USING (site = current_setting('app.current_site', true) OR current_setting('app.current_role', true) = 'admin');
