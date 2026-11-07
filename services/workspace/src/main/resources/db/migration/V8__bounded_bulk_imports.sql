-- MySQL owns import admission, payload and terminal result. Polling is a wakeup;
-- applying documents and recording APPLIED happen in the same transaction.
CREATE TABLE bulk_import_admission (
 id TINYINT PRIMARY KEY,
 CONSTRAINT bulk_import_single_admission CHECK (id = 1)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
INSERT INTO bulk_import_admission(id) VALUES (1);

CREATE TABLE bulk_imports (
 id CHAR(36) PRIMARY KEY,
 workspace_id CHAR(36) NOT NULL,
 idempotency_key VARCHAR(128) NOT NULL,
 request_hash CHAR(64) NOT NULL,
 request_json JSON NOT NULL,
 expected_revision BIGINT NOT NULL,
 applied_revision BIGINT,
 document_count INT NOT NULL,
 status VARCHAR(16) NOT NULL,
 result_json JSON NOT NULL,
 error_code VARCHAR(64),
 error_message VARCHAR(1000),
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 finished_at DATETIME(6),
 UNIQUE KEY bulk_import_idempotency (workspace_id, idempotency_key),
 KEY bulk_import_dispatch (status, created_at, id),
 KEY bulk_import_workspace_status (workspace_id, status),
 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
 CONSTRAINT bulk_import_status CHECK (status IN ('QUEUED','APPLIED','FAILED','CANCELLED')),
 CONSTRAINT bulk_import_document_count CHECK (document_count BETWEEN 1 AND 25),
 CONSTRAINT bulk_import_expected_revision CHECK (expected_revision >= 0)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
