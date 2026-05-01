# Damage cap up & per-hit potency

The six-group bucket model in `README.md` describes how buffs/debuffs
**multiply** the base damage of an attack. This document covers the
*ceiling* the multiplier runs into, and the per-hit input that determines
whether each hit reaches that ceiling.

Every hit of every skill is clamped at the per-hit damage cap before the
hits sum into total damage. Stacking buff multipliers past the cap is
wasted unless the cap itself is raised. So a complete picture of damage
potential is:

```
total_damage  ≈  hits × min(per_hit_potential, per_hit_cap)
```

…where `per_hit_potential` comes from the bucket math (and per-hit
potency, see below) and `per_hit_cap` comes from the team's stacked
"Damage Cap Up" effects.

## What "Damage Cap Up" means

`Damage Cap Up` raises the maximum damage one hit can deal. Values are in
**raw damage units** (e.g. `+100k`), **not percentages** — that's the
distinction from G1/G2/G3 modifiers, which are all percentage scaling.

Units **stack additively** across sources, with no 30% sub-bucket cap:
two `+100k` orbs on the team give `+200k` cap, three give `+300k`, and so
on.

## Sources of Damage Cap Up

### 1. Free, always-available items

Every player can obtain these without gacha. Each grants **+100k cap up**
and a flat ATK boost on top.

| Item | Cap up | Other |
|---|---|---|
| **Orb of King Dulin** | +100k | +200 Phys Atk |
| **Blade of Eternal Flaw** | +100k | +200 Phys Atk, +200 Elem Atk |
| **Sage Helva's Orb** | +100k | +200 Elem Atk |

These are equipment, so each occupies a weapon/orb slot — at most one
contributes per character. A 4-active team can therefore have up to 3 of
these in play (one per attacker who has a free slot for the appropriate
one), giving up to **+300k cap up** before any character-specific cap
sources are counted.

### 2. A4 accessories

Some A4 accessories contribute Damage Cap Up among their other effects.
Example: **Bargello's A4** grants +100k cap up. A4 effects are
character-specific — the cap up only fires when that character is
active.

### 3. Active and passive skills

Some characters have skills that explicitly raise the per-hit cap.
Examples:
- One of **Bargello's** skills.
- One of **Rondo EX's** skills.

These follow the standard skill semantics — actives are turn-limited and
respect Boost Lv scaling; passives are always-on while their condition
holds.

## Cap tier heuristic

A team's total Damage Cap Up across all three source types places it in
one of three rough tiers:

| Total cap up | Tier |
|---|---|
| ≥ 100k | **Good** |
| 50k – 99k | **So-so** |
| < 50k | **Low** |

A team in the **Good** tier can usually convert its full multiplier into
landed damage on every hit; **So-so** teams clip on big hits; **Low**
teams waste a lot of buff stack.

## Per-hit potency and the 240 rule

Skill **potency** is the per-hit power number printed in skill text
(`1x 200 Power`, `2x 150 Power each`, etc.). It's the input to the bucket
math — every multiplier described in `README.md` scales potency, not
total damage.

A useful rule of thumb:

> A skill with **potency ≥ ~120 per hit** combined with **~100% Skill
> Potency Up** (so realized per-hit potency ≥ ~240) is high enough to
> reach the per-hit cap, **provided** the team has enough cap up
> (Good tier above) and a competitive G1/G2/G3 buff stack.

In other words, three conditions must hold for a hit to land at cap:

1. **Base potency** of the skill ≥ ~120.
2. **Skill Potency Up** on the team ≈ +100% (so realized potency ≥ ~240).
3. **Damage Cap Up** ≥ +100k on the team.

When all three hold, total damage ≈ `hits × per_hit_cap`. When any
condition is missing, the team's effective ceiling is lower and the
buff stack is partially wasted.

## Worked example

A 4-hit DPS with `4x 150 Power` skill, on a team carrying:

- 1 Orb of King Dulin (+100k cap)
- 1 A4 with +100k cap
- a 100% Skill Potency Up stack across active and passive sources

```
realized per-hit potency = 150 × (1 + 1.00) = 300        (≥ 240 → reaches cap)
team damage cap up       = 100k + 100k = 200k             (Good tier)
hits                     = 4
buff multiplier (G1..G6) = whatever the rest of the bucket math says
```

Each hit lands at cap; total damage ≈ `4 × per_hit_cap`. Stacking more
G1/G2/G3 buffs beyond what's needed to reach cap on each hit increases
the buffer against future enemy resistances but doesn't increase total
damage on this fight. Stacking *more* cap up (e.g. swapping in a
character with cap-up active) raises the ceiling and converts more of
the buff multiplier into damage.

A 1-hit, 100-potency skill on the same team has `realized = 200`
(below 240), so even with `+200k` cap up the per-hit damage runs out of
*potency*, not cap, and adding more cap up would not help — adding a
Skill Potency Up source would.

## Cross-reference: where this lives in code

- `analysis/patterns.py` records the three free-item names and the +100k
  cap-up constant they each grant.
- `analysis/types.py::ClassifiedEffect` includes `category='damage_cap_up'`
  with magnitude in **raw units** (the convention is documented on the
  dataclass).
- `analysis/damage_estimate.py` aggregates total team cap up from skill
  text, A4 text, and the `cap_orbs` slash-command parameter, and reports
  it alongside the bucket-math multiplier per candidate DPS.
