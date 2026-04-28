"""Project configuration: sheet identity, tab map, color → rarity map, paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
# DB path is overridable so a Railway deploy can point at a mounted volume
# (e.g. COTC_DB_PATH=/data/cotc.sqlite) while local dev keeps data/cotc.sqlite.
DB_PATH = Path(os.environ["COTC_DB_PATH"]) if os.environ.get("COTC_DB_PATH") else DATA_DIR / "cotc.sqlite"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
USER_CONFIG_DIR = Path.home() / ".cotc-search"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.toml"


def _read_user_config() -> dict[str, str]:
    """Parse the simple `key = "value"` lines in ~/.cotc-search/config.toml."""
    out: dict[str, str] = {}
    if not USER_CONFIG_PATH.exists():
        return out
    try:
        for line in USER_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if not sep:
                continue
            out[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def get_setting(env_name: str, toml_key: str, default: str | None = None) -> str | None:
    """Look up a setting: env var first, then ~/.cotc-search/config.toml, then default.

    Lets the same code run on Railway (env-only) and locally (toml-backed)
    without forking. Empty strings are treated as 'unset'.
    """
    val = os.environ.get(env_name)
    if val:
        return val
    val = _read_user_config().get(toml_key)
    if val:
        return val
    return default


def parse_admin_ids(raw: str | None) -> set[int]:
    """Parse a comma-separated list of Discord user IDs into a set of ints.

    Bad/empty entries are silently dropped — invalid IDs just mean fewer
    admins, never a crash on startup.
    """
    if not raw:
        return set()
    out: set[int] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(int(piece))
        except ValueError:
            continue
    return out

SPREADSHEET_ID = "1LF2NbjnMsq8Jo2TSpocu6NN-o9dsUlmd8xCMZpKUHNw"
SPREADSHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"


@dataclass(frozen=True)
class TabSpec:
    gid: int
    name: str
    kind: str            # 'index' | 'release' | 'sea_unique' | 'role'
    role: str | None     # 'warrior'..'dancer' for role tabs
    weapon: str | None   # 'sword'..'fan'
    rarity_band: str | None  # '5*' for ⭐5 tabs, '34' for 3✯&4✯ tabs


TABS: list[TabSpec] = [
    TabSpec(1917707422, "Characters Index",                 "index",      None,         None,    None),
    TabSpec(1980306538, "Release History",                  "release",    None,         None,    None),
    TabSpec(291065169,  "SEA/GL Unique Kits",               "sea_unique", None,         None,    None),
    TabSpec(519845584,  "Warriors ⭐5",                  "role",       "warrior",    "sword", "5*"),
    TabSpec(1629538431, "Merchants ⭐5",                 "role",       "merchant",   "spear", "5*"),
    TabSpec(757461742,  "Thieves ⭐5",                   "role",       "thief",      "dagger","5*"),
    TabSpec(1672823319, "Apothecaries ⭐5",              "role",       "apothecary", "axe",   "5*"),
    TabSpec(1163348187, "Hunters ⭐5",                   "role",       "hunter",     "bow",   "5*"),
    TabSpec(15659830,   "Clerics ⭐5",                   "role",       "cleric",     "staff", "5*"),
    TabSpec(284157275,  "Scholars ⭐5",                  "role",       "scholar",    "tome",  "5*"),
    TabSpec(1697308519, "Dancers ⭐5",                   "role",       "dancer",     "fan",   "5*"),
    TabSpec(2112599282, "Warriors (Swords) 3 & 4",          "role",       "warrior",    "sword", "34"),
    TabSpec(938797442,  "Merchants (Spears) 3 & 4",         "role",       "merchant",   "spear", "34"),
    TabSpec(2021447784, "Thieves (Daggers) 3 & 4",          "role",       "thief",      "dagger","34"),
    TabSpec(671394792,  "Apothecaries (Axes) 3 & 4",        "role",       "apothecary", "axe",   "34"),
    TabSpec(809392400,  "Hunters (Bows) 3 & 4",             "role",       "hunter",     "bow",   "34"),
    TabSpec(1570192166, "Clerics (Staves) 3 & 4",           "role",       "cleric",     "staff", "34"),
    TabSpec(203210803,  "Scholars (Tomes) 3 & 4",           "role",       "scholar",    "tome",  "34"),
    TabSpec(368294896,  "Dancers (Fans) 3 & 4",             "role",       "dancer",     "fan",   "34"),
]

TABS_BY_GID: dict[int, TabSpec] = {t.gid: t for t in TABS}
ROLE_TABS: list[TabSpec] = [t for t in TABS if t.kind == "role"]
WEAPON_TO_ROLE: dict[str, str] = {
    t.weapon: t.role
    for t in ROLE_TABS
    if t.weapon is not None and t.role is not None
}


# Adversary Log CotC — sibling enemy spreadsheet.
ENEMIES_SPREADSHEET_ID = "1Of4zz3rlV973Rt2kzHqoSWjiJmfhb77iMnAYofCT3Gs"
ENEMIES_SPREADSHEET_URL = f"https://docs.google.com/spreadsheets/d/{ENEMIES_SPREADSHEET_ID}"


@dataclass(frozen=True)
class EnemyTabSpec:
    gid: int
    name: str
    category: str           # display label, e.g. 'Lvl 1' or '120 NPCs'
    region: str | None      # 'Osterra' | 'Solistia' | None


# GIDs and metadata discovered via verify/probe_enemies.py. Each Lvl-N tab
# is a "category" of enemies (the level at which the player encounters them).
# 120 NPCs is single-rank; the parser treats it as is_npc=True.
ENEMIES_TABS: list[EnemyTabSpec] = [
    EnemyTabSpec(336420009,  "Lvl 1",            "Lvl 1",            "Osterra"),
    EnemyTabSpec(462109912,  "Lvl 25",           "Lvl 25",           "Osterra"),
    EnemyTabSpec(1105319828, "Lvl 50",           "Lvl 50",           "Osterra"),
    EnemyTabSpec(441922710,  "Lvl 75",           "Lvl 75",           "Osterra"),
    EnemyTabSpec(1544805455, "Solistia Lvl 1",   "Solistia Lvl 1",   "Solistia"),
    EnemyTabSpec(795720982,  "Solistia Lvl 25",  "Solistia Lvl 25",  "Solistia"),
    EnemyTabSpec(1229169620, "Solistia Lvl 50",  "Solistia Lvl 50",  "Solistia"),
    EnemyTabSpec(945280021,  "Solistia Lvl 75",  "Solistia Lvl 75",  "Solistia"),
    EnemyTabSpec(2117870435, "120 NPCs",         "120 NPCs",         "NPCs"),
]
ENEMIES_TABS_BY_GID: dict[int, EnemyTabSpec] = {t.gid: t for t in ENEMIES_TABS}

# Data-source tabs (per-rank stats live here, indexed by encounter name).
ENEMY_DATA_TAB_GIDS: dict[str, int] = {
    "Osterra":  758398692,   # 'Osterra Data'
    "Solistia": 761197564,   # 'Solistia Data'
    "NPCs":     1230510791,  # '120 NPCs Data'
}

# Display tabs whose blocks should be treated as NPCs (single-rank, no dropdown).
ENEMY_NPC_TAB_GIDS: frozenset[int] = frozenset({2117870435})

# Tabs that are not parser inputs: instructional, layout scaffold, image assets.
ENEMY_SKIP_TABS: frozenset[str] = frozenset({
    "Guide", "Template", "Images",
})

# Display-tab name → data-tab encounter key. Populate when the verifier
# reports an unmatched display block. Aliases are preferred over fuzzy
# substring matching because they're self-documenting.
#
# Reasons each entry exists (left = visible name, right = data-tab spelling):
#   community-sheet typos and abbreviations.
ENEMY_NAME_ALIASES: dict[str, str] = {
    "Ring-Sealed Beast":        "RingBeast",
    "Black-Clad Shadow":        "Black Shadow",
    "Ignazio the Hungry Fang":  "Ignazio HUNGY",
    "M'suhi the Viper":         "Msushi",
    "Avar the Conqueror":       "Aval Conqueror",
    "Avar the Bestial Claw":    "Aval",
}


# Color → rarity mapping. The reference sheet legend (row 5):
#   Red    = Base 5★
#   Green  = Free Base 3 → 5★
#   Yellow = Base 4★
#   Blue   = Base 3★
# We canonicalize to a small palette and tolerate small color drift.
RARITY_BY_COLOR_FAMILY = {
    "red":    "5*",
    "green":  "free35",
    "yellow": "4*",
    "blue":   "3*",
    "white":  None,   # blank cells / headers
    "black":  None,
}


def color_family(hex_color: str | None) -> str | None:
    """Coarse-bucket an RGB hex (#RRGGBB) into red/green/yellow/blue/white/black.

    The sheet uses google-default reds/greens/etc. and small variations across
    cells; bucket by which channel dominates rather than exact match.
    """
    if not hex_color:
        return None
    s = hex_color.lstrip("#")
    if len(s) != 6:
        return None
    try:
        r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
    except ValueError:
        return None
    # near-white / near-black
    if r > 230 and g > 230 and b > 230:
        return "white"
    if r < 40 and g < 40 and b < 40:
        return "black"
    # red dominates and others low
    if r >= 150 and g < 120 and b < 120:
        return "red"
    # yellow/orange: r and g both high, b low
    if r >= 180 and g >= 130 and b < 120:
        return "yellow"
    # green dominates
    if g >= 130 and r < 180 and b < 150:
        return "green"
    # blue dominates
    if b >= 140 and r < 150 and g < 180:
        return "blue"
    return None


def rarity_from_color(hex_color: str | None) -> str | None:
    fam = color_family(hex_color)
    return RARITY_BY_COLOR_FAMILY.get(fam) if fam else None


# Manual alias table for characters whose role-tab name disagrees with their
# Characters Index name (typos, alternate transliterations of JP names, etc.).
# Map an *alternate* (role-tab) name to the *canonical* (Index) name. The
# runner consults this before falling back to Levenshtein-≤2 fuzzy matching,
# so it covers cases too far apart for fuzzy (e.g. JP↔EN spellings).
#
# Add new entries here when verify/check.py reports unmatched live blocks.
NAME_ALIASES: dict[str, str] = {
    # role-tab spelling : Index spelling
    "Fior":     "Fiore",
    "Krauser":  "Clauser",
    "Araune":   "Alaune",     # JP↔EN transliteration drift
    "Elrica":   "Erika",      # JP↔EN transliteration drift
    "Tithi":    "Titi",       # SEA spells the dancer 'Tithi'; Index has 'Titi'
}


def canonicalize_name(name: str) -> str:
    """Return the Index canonical name for any role-tab spelling we know about."""
    return NAME_ALIASES.get(name, name)


_ALIAS_LOOKUP_CI: dict[str, str] = {k.casefold(): v for k, v in NAME_ALIASES.items()}


def alias_to_canonical(name: str) -> str | None:
    """Case-insensitive alias lookup. Returns the canonical Index name if
    `name` is a known alias, else None. Used by the bot at query time so
    users can find characters by alternate spellings (e.g. 'Krauser')."""
    if not name:
        return None
    return _ALIAS_LOOKUP_CI.get(name.casefold())


# Discord links and other constants useful in the UI footer.
DISCORD_JP = "https://discord.gg/Ah3xSgtkgd"
DISCORD_GLOBAL = "https://discord.gg/octopath"
