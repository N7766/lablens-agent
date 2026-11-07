-- Serialize new task creation across all workspaces and JVM instances using this database.
-- No mutable counter is needed: active task rows are the source of capacity truth.
CREATE TABLE task_admission_guard (
 id TINYINT NOT NULL PRIMARY KEY,
 CONSTRAINT one_global_admission_guard CHECK (id=1)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
INSERT INTO task_admission_guard(id) VALUES (1);

-- Bound per-workspace admission reads to active rows rather than scanning task history.
CREATE INDEX task_admission_workspace ON tasks(workspace_id,status,created_at);
