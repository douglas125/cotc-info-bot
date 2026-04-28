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
    id                INTEGER PRIMARY KEY,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    status            TEXT NOT NULL,        -- 'running' | 'ok' | 'error'
    error             TEXT,
    forms_count       INTEGER,
    skills_count      INTEGER,
    enemies_count     INTEGER,
    enemy_forms_count INTEGER
);

-- One row per (sync run, source kind); payload is gzipped JSON of the Sheets
-- API response for that sheet. `kind` distinguishes the two pipelines so a
-- single /refresh produces two snapshots under one run.
CREATE TABLE IF NOT EXISTS raw_snapshots (
    sync_run_id     INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL DEFAULT 'characters',  -- 'characters' | 'enemies'
    payload_json    BLOB NOT NULL,
    PRIMARY KEY (sync_run_id, kind)
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

-- Community-submitted corrections / inconsistency reports. Survives /refresh
-- (intentionally NOT in repo.clear_data_tables); cleared via /feedback_clear.
CREATE TABLE IF NOT EXISTS feedback_submissions (
    id              INTEGER PRIMARY KEY,
    submitted_at    TEXT NOT NULL,        -- ISO-8601 UTC
    user_id         INTEGER NOT NULL,     -- Discord user ID
    username        TEXT NOT NULL,        -- display name at submission time
    guild_id        INTEGER,              -- NULL in DMs
    feedback_text   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feedback_submitted
    ON feedback_submissions(submitted_at DESC);
CREATE INDEX IF NOT EXISTS ix_feedback_user_time
    ON feedback_submissions(user_id, submitted_at DESC);


-- ===========================================================================
-- Enemies (Adversary Log CotC sheet — separate spreadsheet, parallel pipeline)
-- ===========================================================================
--
-- Modeling decisions (driven by verify/probe_enemies.py findings):
--  * The Lvl-N display tabs hold the user-facing name + a per-cell rank
--    dropdown. The displayed stats are whichever rank was last selected, so
--    they cannot drive a multi-rank /enemy. The *Data tabs hold all 6 ranks
--    × N members per encounter, indexed by encounter name.
--  * One `enemies` row per (canonical_name, category) — the same encounter
--    can appear in multiple Lvl-N tabs and we want each as a separate entry.
--  * One `enemy_forms` row per rank (Rank1/2/3, EX1/2/3, or 'Default' for
--    NPCs which only have one stat row).
--  * Stats stored long-form in `enemy_member_stats`: variable encounter
--    composition (1 leader, optionally 1-2 adds) is data-driven by position.
--  * Weakness icons are inserted images, not API-readable. Break-shield
--    counts are folded into the stats grid as the 'Shields' stat. The /enemy
--    embed adds a "see sheet for weakness icons" link.

CREATE TABLE IF NOT EXISTS enemies (
    id              INTEGER PRIMARY KEY,
    canonical_name  TEXT NOT NULL,        -- e.g. 'Sly Leader Lloris'
    category        TEXT NOT NULL,        -- 'Lvl 1' | ... | 'Solistia Lvl 75' | '120 NPCs'
    region          TEXT,                 -- 'Osterra' | 'Solistia' | 'NPCs'
    sheet_gid       INTEGER,
    source_row      INTEGER,
    name_color_hex  TEXT,
    hyperlink_url   TEXT,                 -- '#gid=...&range=...' anchor into the sheet
    is_npc          INTEGER NOT NULL DEFAULT 0,
    UNIQUE(canonical_name, category, sheet_gid)
);
CREATE INDEX IF NOT EXISTS ix_enemies_category ON enemies(category);
CREATE INDEX IF NOT EXISTS ix_enemies_name ON enemies(canonical_name);

CREATE TABLE IF NOT EXISTS enemy_forms (
    id              INTEGER PRIMARY KEY,
    enemy_id        INTEGER NOT NULL REFERENCES enemies(id) ON DELETE CASCADE,
    rank            TEXT NOT NULL,         -- 'Rank1'|'Rank2'|'Rank3'|'EX1'|'EX2'|'EX3'|'Default'
    rank_order      INTEGER NOT NULL,      -- 1..6 for ranked, 0 for NPC 'Default'
    UNIQUE(enemy_id, rank)
);
CREATE INDEX IF NOT EXISTS ix_enemy_forms_enemy ON enemy_forms(enemy_id);

CREATE TABLE IF NOT EXISTS enemy_member_stats (
    id              INTEGER PRIMARY KEY,
    form_id         INTEGER NOT NULL REFERENCES enemy_forms(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,      -- 0 = leader, 1+ = adds
    member_name     TEXT,                  -- 'Leader Lloris' | 'Mini Lloris' | NULL
    stat_name       TEXT NOT NULL,         -- 'Shields'|'HP'|'P. Atk'|'P. Def'|'E. Atk'|...
    stat_value      TEXT NOT NULL,         -- TEXT — values include '-' or large ints
    UNIQUE(form_id, position, stat_name)
);
CREATE INDEX IF NOT EXISTS ix_enemy_stats_form ON enemy_member_stats(form_id);

-- Free-text search across enemy canonical names, categories, and member names.
CREATE VIRTUAL TABLE IF NOT EXISTS enemies_fts USING fts5(
    enemy_id UNINDEXED,
    canonical_name,
    category,
    member_names,
    tokenize = 'unicode61 remove_diacritics 2'
);
