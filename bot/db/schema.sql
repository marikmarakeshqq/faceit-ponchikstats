PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tracked_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    faceit_player_id TEXT NOT NULL UNIQUE,
    nickname TEXT NOT NULL,
    avatar_url TEXT,
    country TEXT,
    added_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notification_chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    notifications_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processed_match_teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    notified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, team_id)
);

CREATE TABLE IF NOT EXISTS player_recent_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    match_id TEXT NOT NULL,
    map_name TEXT,
    score TEXT,
    kd REAL,
    adr REAL,
    hs REAL,
    kills INTEGER,
    deaths INTEGER,
    assists INTEGER,
    result TEXT,
    played_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(player_id, match_id)
);

CREATE TABLE IF NOT EXISTS notification_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    match_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    message_text TEXT,
    media_type TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tracked_players_nickname
    ON tracked_players(nickname);

CREATE INDEX IF NOT EXISTS idx_recent_matches_player_played
    ON player_recent_matches(player_id, played_at DESC);

CREATE INDEX IF NOT EXISTS idx_notification_logs_created
    ON notification_logs(created_at DESC);
