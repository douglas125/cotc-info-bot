-- CotC character sheet local DB schema.
-- One canonical character can have multiple forms (rarity / EX / server variants).

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS characters (
    id              INTEGER PRIMARY KEY,
    canonical_name  TEXT NOT NULL UNIQUE,
    base_role       TEXT,
    base_weapon     TEXT
);

CREATE TABLE IF NOT EXISTS character_forms (
    id              INTEGER PRIMARY KEY,
    character_id    INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL,
    rarity          TEXT,                  -- '5*' | '4*' | '3*' | 'free35' | NULL
    variant_kind    TEXT NOT NULL DEFAULT 'base',
    server          TEXT NOT NULL DEFAULT 'global',
    level_cap       INTEGER,
    sheet_gid       INTEGER,
    source_row      INTEGER,
    name_color_hex  TEXT,
    hyperlink_url   TEXT,
    UNIQUE(display_name, server, sheet_gid)
);
CREATE INDEX IF NOT EXISTS ix_forms_rarity_role
    ON character_forms(rarity, sheet_gid);

CREATE TABLE IF NOT EXISTS character_affinities (
    id              INTEGER PRIMARY KEY,
    form_id         INTEGER NOT NULL REFERENCES character_forms(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,       -- 'weapon' | 'element' | 'weakness' | 'trait'
    icon_label      TEXT,
    icon_url        TEXT,
    UNIQUE(form_id, kind, icon_label)
);
CREATE INDEX IF NOT EXISTS ix_aff_kind_label
    ON character_affinities(kind, icon_label);

CREATE TABLE IF NOT EXISTS skills (
    id              INTEGER PRIMARY KEY,
    form_id         INTEGER NOT NULL REFERENCES character_forms(id) ON DELETE CASCADE,
    slot_order      INTEGER NOT NULL,
    name            TEXT,
    sp_cost         INTEGER,
    kind            TEXT,            -- 'active'|'passive'|'divine'|'ex'|'ultimate'|'latent'
    learn_board     INTEGER,         -- prestige board (1..6) for active/passive rows
    tier_level      INTEGER,         -- upgrade tier (1, 10, 20) for ultimate rows
    initial_use     INTEGER,         -- latent: turns before first trigger
    cooldown        INTEGER,         -- latent: turns between uses
    description     TEXT,
    power_min       INTEGER,
    power_max       INTEGER,
    hits            INTEGER
);
CREATE INDEX IF NOT EXISTS ix_skills_form ON skills(form_id);

CREATE TABLE IF NOT EXISTS equipment (
    id              INTEGER PRIMARY KEY,
    form_id         INTEGER NOT NULL REFERENCES character_forms(id) ON DELETE CASCADE,
    slot            TEXT,
    name            TEXT,
    description     TEXT,
    is_exclusive    INTEGER NOT NULL DEFAULT 0  -- 1 = character-exclusive A4 accessory
);
CREATE INDEX IF NOT EXISTS ix_equipment_form ON equipment(form_id);

CREATE TABLE IF NOT EXISTS character_profile (
    form_id         INTEGER PRIMARY KEY REFERENCES character_forms(id) ON DELETE CASCADE,
    splash_art_url  TEXT,
    self_buffs_text TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,        -- 'running' | 'ok' | 'error'
    error           TEXT,
    forms_count     INTEGER,
    skills_count    INTEGER
);

-- One row per sync run; payload is gzipped JSON of the full Sheets API response.
CREATE TABLE IF NOT EXISTS raw_snapshots (
    sync_run_id     INTEGER PRIMARY KEY REFERENCES sync_runs(id) ON DELETE CASCADE,
    payload_json    BLOB NOT NULL
);

-- Free-text search over canonical names, display names, skill text and equipment text.
CREATE VIRTUAL TABLE IF NOT EXISTS characters_fts USING fts5(
    form_id UNINDEXED,
    canonical_name,
    display_name,
    skill_text,
    equipment_text,
    tokenize = 'unicode61 remove_diacritics 2'
);
