-- Checkpoint rows are append-only receipts, fenced by the current task owner.
ALTER TABLE tasks ADD COLUMN parent_task_id CHAR(36) NULL,
 ADD UNIQUE KEY one_recovery_child (parent_task_id);

CREATE TABLE task_checkpoints (
 task_id CHAR(36) NOT NULL,
 revision INT NOT NULL,
 kind VARCHAR(24) NOT NULL,
 operation_id VARCHAR(120),
 envelope_sha256 CHAR(64) NOT NULL,
 state_sha256 CHAR(64),
 state_json MEDIUMTEXT,
 state_bytes INT NOT NULL DEFAULT 0,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY (task_id,revision),
 CONSTRAINT checkpoint_task FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
