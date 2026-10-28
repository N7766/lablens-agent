CREATE INDEX task_expiry ON tasks(status, lease_until);
