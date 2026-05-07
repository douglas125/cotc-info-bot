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
    alignment       TEXT,                  -- e.g. 'Glory', 'Sovereign'; printed below the portrait
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
    kind            TEXT,            -- 'active'|'passive'|'divine'|'tp_passive'|'ex'|'ultimate'|'latent'
    learn_board     INTEGER,         -- prestige board (1..6) for active/passive rows
    tier_level      INTEGER,         -- upgrade tier (1, 10, 20) for ultimate rows
    initial_use     INTEGER,         -- latent: turns before first trigger
    cooldown        INTEGER,         -- latent: turns between uses
    description     TEXT,
    power_min       INTEGER,
    power_max       INTEGER,
    hits            INTEGER,
    max_uses         INTEGER,        -- ex: max number of uses per battle
    unlock_condition TEXT            -- ex: condition text gating the skill
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

-- Stat boosts granted by an A4 accessory. Each accessory has 0-4 stats,
-- encoded in the sheet as `=ATK`/`=SP`/... formula icons paired with
-- numeric values (negatives allowed; e.g. "Secrets of Sorcery" gives ATK -200).
CREATE TABLE IF NOT EXISTS equipment_stats (
    id              INTEGER PRIMARY KEY,
    equipment_id    INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    stat_name       TEXT NOT NULL,    -- 'ATK'|'MAG'|'SP'|'HP'|'SPD'|'DEF'|'MDEF'|'CRIT'
    stat_value      INTEGER NOT NULL,
    stat_order      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_equipment_stats_equipment
    ON equipment_stats(equipment_id);

CREATE TABLE IF NOT EXISTS character_profile (
    form_id         INTEGER PRIMARY KEY REFERENCES character_forms(id) ON DELETE CASCADE,
    splash_art_url  TEXT,
    self_buffs_text TEXT
);

-- Lv100 / Lv120 base stats per character form. The role tab shows two
-- columns "Lv100" / "Lv120"; older 3*/4*/5* characters can be promoted to
-- 6* and gain a Lv120 column too, so both levels are populated for almost
-- every form. Stat names come from `=HP`/`=SP`/`=ATK`/... formulas in the
-- icon column; missing levels are simply skipped (no NULL rows).
CREATE TABLE IF NOT EXISTS character_stats (
    id              INTEGER PRIMARY KEY,
    form_id         INTEGER NOT NULL REFERENCES character_forms(id) ON DELETE CASCADE,
    level           INTEGER NOT NULL,            -- 100 | 120
    stat_name       TEXT NOT NULL,               -- 'HP'|'SP'|'ATK'|'DEF'|'MAG'|'MDEF'|'ACC'|'SPD'|'CRIT'|'EVA'
    stat_value      INTEGER NOT NULL,
    stat_order      INTEGER NOT NULL DEFAULT 0,  -- preserves icon row order
    UNIQUE(form_id, level, stat_name)
);
CREATE INDEX IF NOT EXISTS ix_character_stats_form
    ON character_stats(form_id, level, stat_order);

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

-- Per-command, per-day invocation counter for /character and /enemy.
-- Survives /refresh (intentionally NOT in repo.clear_*_tables).
CREATE TABLE IF NOT EXISTS command_usage_daily (
    command_name  TEXT NOT NULL,            -- 'character' | 'enemy'
    usage_date    TEXT NOT NULL,            -- 'YYYY-MM-DD' (UTC)
    count         INTEGER NOT NULL,
    PRIMARY KEY (command_name, usage_date)
);

-- Wiki-curated sprite URL per canonical character. Hot-linked from
-- static.wikia.nocookie.net via embed.set_thumbnail; no local files.
-- Survives /refresh (intentionally NOT in repo.clear_*_tables) — this is
-- community-curated state, not sheet-derived. Populated by
-- `python -m scripts.refresh_sprite_urls`. `canonical_name` is a soft
-- FK to characters.canonical_name (no enforced FK so the table survives
-- a refresh that temporarily empties characters during the wipe step).
CREATE TABLE IF NOT EXISTS character_sprites (
    canonical_name TEXT PRIMARY KEY,
    sprite_url     TEXT NOT NULL,
    source         TEXT,                     -- 'wikia' | 'manual'
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Pre-parsed Arena fight notes used by /enemy. This is tracked app data,
-- not sheet-derived data, so it survives /refresh. Seeded from
-- db/seed/arena_fight_notes.json during bootstrap.
CREATE TABLE IF NOT EXISTS arena_fight_notes (
    fight_key          TEXT PRIMARY KEY,
    display_name       TEXT NOT NULL,
    enemy_aliases_json TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    source_updated_at  TEXT,
    summary            TEXT NOT NULL,
    mechanics          TEXT NOT NULL,
    strategy           TEXT NOT NULL,
    actions_json       TEXT NOT NULL DEFAULT '[]'
);


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
    -- NFKC-normalized + diacritic-stripped + casefolded `canonical_name`,
    -- used by the bot's autocomplete and exact-name resolver so a user
    -- typing 'Kaine?' on an English keyboard still matches 'Kainé?', and
    -- '9S?' matches the fullwidth '９Ｓ？'. Repopulated on every upsert.
    search_key      TEXT,
    UNIQUE(canonical_name, category, sheet_gid)
);
CREATE INDEX IF NOT EXISTS ix_enemies_category ON enemies(category);
CREATE INDEX IF NOT EXISTS ix_enemies_name ON enemies(canonical_name);
CREATE INDEX IF NOT EXISTS ix_enemies_search_key ON enemies(search_key);

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

-- Weakness icons live in the display tabs as named-range formulas like
-- '=Sword', '=Wind', '=Dark'. We pull the label from `userEnteredValue.formulaValue`.
CREATE TABLE IF NOT EXISTS enemy_weaknesses (
    id              INTEGER PRIMARY KEY,
    form_id         INTEGER NOT NULL REFERENCES enemy_forms(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    weakness_label  TEXT NOT NULL,         -- 'Sword'|'Axe'|'Fire'|'Ice'|...
    slot_order      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_enemy_weak_form ON enemy_weaknesses(form_id);

-- Free-text search across enemy canonical names, categories, and member names.
CREATE VIRTUAL TABLE IF NOT EXISTS enemies_fts USING fts5(
    enemy_id UNINDEXED,
    canonical_name,
    category,
    member_names,
    tokenize = 'unicode61 remove_diacritics 2'
);


-- ===========================================================================
-- Pets (Seed Story Content sheet — third spreadsheet, parallel pipeline)
-- ===========================================================================
--
-- Modeling decisions:
--  * One `pets` row per (canonical_name, source_row). The Pet List sheet has
--    duplicate English names (e.g. 'White Rabbit' from Login + Quest); the
--    source row is the natural disambiguator — same name on two different
--    rows means two different pets in-game.
--  * No `pet_forms` table — pets have no rank/variant axis.
--  * Stats are 8 fixed columns on `pets` (HP, SP, Patk, Pdef, Matk, Mdef,
--    Crit, Speed). Long-form would just be re-pivoted at every read site.
--  * The ability cell packs effect text + optional `Max Boost: …` +
--    `Turn Preparation: N (Lv10: N-1)` + `Turn Cooldown: M (Lv5: M-1)`.
--    The parser splits these out into typed columns so the embed can format
--    each line cleanly without re-parsing.
CREATE TABLE IF NOT EXISTS pets (
    id              INTEGER PRIMARY KEY,
    canonical_name  TEXT NOT NULL,        -- English, e.g. 'Red Brown Cat'
    display_name_jp TEXT,                 -- raw cell, e.g. '赤茶 (Red Brown Cat)'
    source_text     TEXT,                 -- "how to obtain" cell
    ability_text    TEXT,                 -- effect lines (1-3) before Max Boost
    max_boost       TEXT,                 -- 'Lv2'/'Lv3'/'Lv4' or NULL
    prep_base       INTEGER,              -- Turn Preparation base
    prep_lv10       INTEGER,              -- Turn Preparation at Lv10 (NULL if absent)
    cooldown_base   INTEGER,              -- Turn Cooldown base
    cooldown_lv5    INTEGER,              -- Turn Cooldown at Lv5 (NULL if absent)
    hp INTEGER, sp INTEGER,
    patk INTEGER, pdef INTEGER,
    matk INTEGER, mdef INTEGER,
    crit INTEGER, speed INTEGER,
    sheet_gid       INTEGER,
    source_row      INTEGER,              -- 0-based row of the name cell
    name_color_hex  TEXT,
    hyperlink_url   TEXT,                 -- '#gid=...&range=A<row>' anchor
    UNIQUE(canonical_name, source_row)
);
CREATE INDEX IF NOT EXISTS ix_pets_name ON pets(canonical_name);

-- Free-text search across pet name (EN + JP) and ability/source text.
CREATE VIRTUAL TABLE IF NOT EXISTS pets_fts USING fts5(
    pet_id UNINDEXED,
    canonical_name,
    display_name_jp,
    ability_text,
    source_text,
    tokenize = 'unicode61 remove_diacritics 2'
);
