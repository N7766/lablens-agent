-- Existing workspaces deliberately receive no memberships. An authenticated admin must
-- explicitly adopt an unowned workspace; the first login never claims legacy data.
CREATE TABLE auth_users (
    id CHAR(36) NOT NULL PRIMARY KEY,
    username VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    password_hash VARCHAR(100) NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE TABLE auth_sessions (
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    user_id CHAR(36) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at TIMESTAMP(6) NOT NULL,
    CONSTRAINT fk_auth_session_user FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    INDEX auth_session_expiry (expires_at)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE TABLE workspace_members (
    workspace_id CHAR(36) NOT NULL,
    user_id CHAR(36) NOT NULL,
    role VARCHAR(8) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (workspace_id,user_id),
    CONSTRAINT fk_member_workspace FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    CONSTRAINT fk_member_user FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    CONSTRAINT valid_member_role CHECK (role IN ('OWNER','EDITOR','VIEWER')),
    INDEX member_user_workspaces (user_id,workspace_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE TABLE auth_login_buckets (
    bucket_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    window_minute BIGINT NOT NULL,
    attempts INT NOT NULL
) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
