-- Shared workspace memory is explicit user input, never an automatically trusted fact.
ALTER TABLE workspaces ADD COLUMN memory_revision BIGINT NOT NULL DEFAULT 0;
CREATE TABLE workspace_memories (
    id CHAR(36) NOT NULL PRIMARY KEY,
    workspace_id CHAR(36) NOT NULL,
    memory_key VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version BIGINT NOT NULL,
    created_by CHAR(36) NOT NULL,
    document_id VARCHAR(128) NOT NULL,
    document_version BIGINT NOT NULL,
    document_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    snapshot_id VARCHAR(80) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_quote TEXT NOT NULL,
    note TEXT NOT NULL,
    expires_at TIMESTAMP(6) NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_memory_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    CONSTRAINT fk_memory_creator FOREIGN KEY (created_by) REFERENCES auth_users(id),
    CONSTRAINT valid_memory_versions CHECK (version > 0 AND document_version > 0),
    UNIQUE KEY memory_workspace_key (workspace_id,memory_key)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
