ALTER TABLE tasks
    ADD COLUMN cancel_requested_at TIMESTAMP(6) NULL,
    ADD COLUMN cancel_acknowledged_at TIMESTAMP(6) NULL;
