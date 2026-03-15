-- ================================================================
-- OLYMPIA AUTH DATABASE
-- Core auth tables: users, sessions, audit_log
-- ================================================================

DROP DATABASE IF EXISTS olympia_auth;
CREATE DATABASE olympia_auth;
USE olympia_auth;

-- ==========================================
-- users: login accounts, maps to olympia_track.Member
-- ==========================================
CREATE TABLE users (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          ENUM('Admin', 'Player', 'Coach') NOT NULL DEFAULT 'Player',
    member_id     INT UNIQUE,          -- FK into olympia_track.Member (nullable for pure admins)
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- sessions: JWT revocation list
-- ==========================================
CREATE TABLE sessions (
    session_id  INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    token_hash  VARCHAR(255) NOT NULL UNIQUE,    -- SHA-256 of the JWT
    issued_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP NOT NULL,
    is_revoked  BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- ==========================================
-- audit_log: tamper-evident hash-chained log
-- ==========================================
CREATE TABLE audit_log (
    log_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    timestamp   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    user_id     INT,                         -- NULL for unauthenticated attempts
    username    VARCHAR(50),
    action      VARCHAR(20)  NOT NULL,        -- INSERT, UPDATE, DELETE, SELECT, LOGIN, etc.
    table_name  VARCHAR(50),
    record_id   VARCHAR(100),                -- PK of affected row (as string)
    status      ENUM('SUCCESS','FAILURE','UNAUTHORIZED') NOT NULL,
    details     TEXT,                         -- JSON blob of changes
    ip_address  VARCHAR(45),                  -- IPv4 or IPv6
    prev_hash   VARCHAR(64)  NOT NULL,        -- SHA-256 of previous log entry
    entry_hash  VARCHAR(64)  NOT NULL         -- SHA-256 of THIS entry's data + prev_hash
);
