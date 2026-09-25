"""Deterministic accessory effect vocabulary shared by import and search.

Only verified game descriptions feed these rules. Tier commentary is never
evidence of an effect. Unknown wording remains searchable as ordinary text.
"""
from __future__ import annotations

import re
import unicodedata

EFFECTS = {
    'damage_cap': 'Damage cap up',
    'bp_recovery': 'BP recovery up',
    'bp_restore': 'BP restoration',
    'hp_regen': 'HP regeneration',
    'sp_regen': 'SP regeneration',
    'ultimate': 'Ultimate gauge fill',
    'barrier': 'HP barrier',
    'dead_aim': 'Guaranteed critical hits / Dead Aim',
    'elemental_crit': 'Elemental critical hits',
    'res_down': 'Enemy resistance down',
    'buff_duration': 'Buff duration extension',
    'debuff_duration': 'Debuff duration extension',
}
TIERS = ('S+', 'S', 'A', 'B', 'C', 'D', 'Unrated')


def name_key(text: str) -> str:
    text = unicodedata.normalize('NFKD', text).casefold()
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(text.translate(str.maketrans({'’': "'", '‘': "'"})).split())


# Applied to individual lines, keeping separately stated penalties out of
# positive-effect classification.
_RULES = {
    'damage_cap': r'\b(?:raise|grant\w*|increase)\b[^.!\n]*\b(?:dmg\.?|damage)\s+(?:cap|limit)\b',
    'bp_recovery': r'\b(?:raise|grant\w*|increase)\b[^!\n]*\bbp recovery\b|\bbp recovery\b[^!\n]*\bincreased\b',
    'bp_restore': r'\b(?:restore|recover)\b[^.!\n]*\b\d+\s*bp\b',
    'hp_regen': r'\b(?:grant\w*|gain)\b[^!\n]*\b(?:automatic hp recovery|hp regen\w*|automatic sp recovery[^!\n]*and hp recovery)\b',
    'sp_regen': r'\b(?:grant\w*|gain)\b[^!\n]*\b(?:automatic sp recovery|sp regen\w*)\b',
    'ultimate': r'\bfill\b[^!\n]*\bultimate[^!\n]*\bgauge\b|\bultimate[^!\n]*\bgauge\s+(?:filled|increased)\b',
    'barrier': r'\b(?:grant\w*|gain)\b[^!\n]*\bhp barrier\b',
    'dead_aim': r'\b(?:grant\w*|gain)\b[^!\n]*\bdead aim\b',
    'elemental_crit': r'\b(?:grant\w*|gain|allow\w*)\b[^!\n]*\b(?:critical (?:hits|damage) with elemental|elemental[^!\n]*critical hits)\b',
    'res_down': r'\blower\b[^!\n]*\b(?:res\.(?=\s)|resistance\b)|\bres\.[^!\n]*\breduced\b',
    'buff_duration': r'\bextend\b[^!\n]*\bduration\b[^!\n]*\baugmenting\b',
    'debuff_duration': r'\bextend\b[^!\n]*\bduration\b[^!\n]*\benfeebling\b',
}
# Periods in Phys./Elem./Dmg./Res. are abbreviations, not sentence boundaries.
_ABBREV = re.compile(r'\b(phys|elem|dmg|res|atk|def|crit|spd)\.', re.I)


def effect_matches(text: str) -> dict[str, str]:
    matches = {}
    for line in text.splitlines():
        for key, pattern in _RULES.items():
            if key == 'res_down' and not re.search(r'\b(?:enemy|enemies|foes?)\b', text, re.I):
                # A resistance penalty without an enemy target (Cursed Shield)
                # must not be advertised as an offensive debuff.
                continue
            # Resistance rules intentionally retain the Res. abbreviation.
            candidate = line if key == 'res_down' else _ABBREV.sub(r'\1', line)
            if re.search(r'\b(?:prevent|cannot|does not (?:grant|raise)|reduce .*recovery)\b', candidate, re.I):
                continue
            if re.search(pattern, candidate, re.I):
                matches.setdefault(key, line.strip())
    return matches


# Phrase replacements produce single safe FTS tokens. Synonyms are indexed
# from detected effects, not raw descriptions, so "BP recovery" cannot make
# a prevention effect match the positive BP recovery category.
_ALIASES = {
    'damage_cap': r'\b(?:dmg\.?|damage)\s+(?:cap|limit)(?:\s+up)?\b',
    'bp_recovery': r'\bbp recovery(?: up)?\b',
    'bp_restore': r'\bbp restor(?:ation|e)\b',
    'hp_regen': r'\b(?:hp regen(?:eration)?|automatic hp recovery)\b',
    'sp_regen': r'\b(?:sp regen(?:eration)?|automatic sp recovery)\b',
    'ultimate': r'\bultimate gauge fill\b',
    'barrier': r'\bhp barrier\b',
    'dead_aim': r'\b(?:dead aim|guaranteed crit(?:ical hits)?)\b',
    'elemental_crit': r'\belemental crit(?:ical hits)?\b',
    'res_down': r'\b(?:enemy )?resistance down\b',
    'buff_duration': r'\bbuff duration extension\b',
    'debuff_duration': r'\bdebuff duration extension\b',
}


def text_query(text: str) -> str:
    text = name_key(text)
    for key, pattern in _ALIASES.items():
        text = re.sub(pattern, 'effect' + key.replace('_', ''), text, flags=re.I)
    return ' AND '.join('"' + token + '"*' for token in re.findall(r'\w+', text))


def effect_tokens(keys) -> str:
    return ' '.join('effect' + key.replace('_', '') for key in keys)
