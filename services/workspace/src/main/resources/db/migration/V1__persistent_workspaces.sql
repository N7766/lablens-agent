CREATE TABLE workspaces (
 id CHAR(36) PRIMARY KEY,
 name VARCHAR(160) NOT NULL,
 revision BIGINT NOT NULL DEFAULT 0,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE documents (
 workspace_id CHAR(36) NOT NULL,
 id VARCHAR(128) NOT NULL,
 title VARCHAR(300) NOT NULL,
 body MEDIUMTEXT NOT NULL,
 source VARCHAR(1000) NOT NULL,
 version BIGINT NOT NULL,
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY (workspace_id, id),
 FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE document_versions (
 workspace_id CHAR(36) NOT NULL,
 document_id VARCHAR(128) NOT NULL,
 revision BIGINT NOT NULL,
 title VARCHAR(300) NOT NULL,
 body MEDIUMTEXT NOT NULL,
 source VARCHAR(1000) NOT NULL,
 deleted BOOLEAN NOT NULL DEFAULT FALSE,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 PRIMARY KEY (workspace_id, document_id, revision),
 FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE tasks (
 id CHAR(36) PRIMARY KEY,
 workspace_id CHAR(36) NOT NULL,
 idempotency_key VARCHAR(128) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 request_json JSON NOT NULL,
 snapshot_revision BIGINT NOT NULL,
 snapshot_json JSON NOT NULL,
 snapshot_document_count INT NOT NULL,
 status VARCHAR(16) NOT NULL,
 owner VARCHAR(100),
 lease_until DATETIME(6),
 result_json JSON,
 error_code VARCHAR(64),
 error_message VARCHAR(1000),
 cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
 usage_unknown BOOLEAN NOT NULL DEFAULT FALSE,
 worker_ms BIGINT,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 started_at DATETIME(6),
 finished_at DATETIME(6),
 UNIQUE KEY task_idempotency (workspace_id, idempotency_key),
 KEY task_dispatch (status, created_at),
 KEY task_workspace (workspace_id, created_at),
 FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
