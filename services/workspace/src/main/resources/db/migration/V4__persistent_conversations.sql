CREATE TABLE conversations (
 id CHAR(36) PRIMARY KEY,
 workspace_id CHAR(36) NOT NULL,
 title VARCHAR(160) NOT NULL,
 revision BIGINT NOT NULL DEFAULT 0,
 state_json JSON NOT NULL,
 created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 UNIQUE KEY conversation_workspace_id (workspace_id,id),
 KEY conversation_workspace_updated (workspace_id,updated_at),
 FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

ALTER TABLE tasks
 ADD COLUMN conversation_id CHAR(36),
 ADD COLUMN conversation_seq BIGINT,
 ADD UNIQUE KEY conversation_turn (conversation_id,conversation_seq),
 ADD CONSTRAINT task_conversation_workspace FOREIGN KEY (workspace_id,conversation_id) REFERENCES conversations(workspace_id,id);
