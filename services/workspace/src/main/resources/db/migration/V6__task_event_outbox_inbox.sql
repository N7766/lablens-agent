-- Notifications carry immutable task references, never request text or document bodies.
-- Audit rows have the same lifetime as their task; production does not delete tasks.
CREATE TABLE task_event_outbox (
 event_id CHAR(36) PRIMARY KEY,
 task_id CHAR(36) NOT NULL,
 workspace_id CHAR(36) NOT NULL,
 snapshot_ref VARCHAR(64) NOT NULL,
 schema_version INT NOT NULL,
 event_type VARCHAR(32) NOT NULL,
 state VARCHAR(16) NOT NULL DEFAULT 'PENDING',
 attempts INT NOT NULL DEFAULT 0,
 owner VARCHAR(100),
 lease_token CHAR(36),
 lease_until DATETIME(6),
 next_attempt_at DATETIME(6) NULL DEFAULT CURRENT_TIMESTAMP(6),
 last_error VARCHAR(64),
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 published_at DATETIME(6),
 UNIQUE KEY one_task_available_event (task_id),
 KEY outbox_dispatch (next_attempt_at,event_id),
 KEY outbox_state (state,created_at),
 KEY outbox_expired_lease (state,lease_until),
 KEY outbox_created (created_at,event_id),
 CONSTRAINT task_event_task FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE task_event_inbox (
 event_id CHAR(36) PRIMARY KEY,
 task_id CHAR(36) NOT NULL,
 disposition VARCHAR(16) NOT NULL,
 task_status VARCHAR(16) NOT NULL,
 accepted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 KEY inbox_created (accepted_at,event_id),
 KEY inbox_disposition (disposition),
 CONSTRAINT inbox_outbox_event FOREIGN KEY (event_id) REFERENCES task_event_outbox(event_id) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
