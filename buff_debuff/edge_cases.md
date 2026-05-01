# Edge cases and nuances

The model in `README.md` covers the common case. This file documents the
nuances and exceptions that don't fit cleanly into the six-group
diagram.

## Boost Lv

Active skills frequently scale with the player's Boost Lv (1–4). The
scaling pattern is **per skill** — there is no single rule. Read the
skill description to know what scales.

Common patterns:

- **Duration only scales.** Skill text shows a single % and a turn range.
  - e.g., `Frontrow 20% Atk/Mag Up for 2-5 turns based on Boost Lv` → 20% at every Boost Lv; 2 turns at Lv 1, 5 turns at Lv 4.
- **Both % and duration scale.** Skill text shows both ranges.
  - e.g., `Frontrow 10–25% Sword Damage Up for 2-5 turns based on Boost Lv` (hypothetical) → both ends scale together.
- **Number of hits scales.** Common on damage skills; the buff/debuff applied is independent of the hit count, but the hit count grows with Boost.
- **Neither scales.** Some skills are flat regardless of Boost Lv.

When computing the bucket math, use the **realized %** at the Boost Lv
the skill is being cast at, not the max-Boost text-display value.

> When the same skill is cast at different Boost Lv on different turns,
> the **same-skill rule** still applies (rule 1 in `README.md`): only
> duration extends; potency does not stack — but if the second cast is
> at a higher Boost, the realized % is the higher of the two.

## Special-case caps (override the 30% default)

Per the meowdb article, four contexts use a cap that is **not** 30%.
These are exceptions; the 30% sub-bucket cap is the default everywhere
else.

| Context | Cap | Notes |
|---|---|---|
| Hell weapons | **200%** | The "damage up vs Hell enemies" final-multiplier pool can stack up to 200% before further additions are ignored. |
| Status ailment resistance | **100%** | Resist-rate buffs against blind/poison/sleep/etc. cap at 100% (immunity). |
| Enemy buffs/debuffs on player | **50%** | When an enemy applies its own buff/debuff to a player, the per-sub-bucket cap is 50% rather than 30%. (Article note: enemy reapplications also stack potency, unlike player-to-player which only extends duration.) |
| EXP / Leaves bonuses | **50%** | Out-of-combat economy buffs cap at 50%. |

These are documented as the user has confirmed them. If a future game
update changes a cap, update this table and `README.md` accordingly.

## Crit detail

The image lists Critical (1.25× by default) as a final multiplier
"unless stated otherwise". There are three distinct things called
"crit" in the game:

- **Crit chance Up.** Boosts the probability of rolling a crit. Lives in
  **G1** as a stat-style buff. Sub-bucket capped at 30% (Active Crit
  chance Up; Passive Crit chance Up; Ult Crit chance Up — each its own
  sub-bucket). Does not appear in the damage formula directly — only in
  the probability of taking the crit branch.

- **Crit Damage Up.** Boosts the crit multiplier itself. Lives in the
  **Crit final-multiplier pool**, *not* G1. Uncapped — accumulates
  additively across sources.

  ```
  Crit_multiplier = 1.25 + Σ Crit Damage Up
  ```

  Applied **only** when the attack is a crit (rolled or guaranteed).

- **Guaranteed Critical** ("Forced Critical"). A skill effect that forces
  the crit branch to apply for that attack regardless of crit chance.
  Does not by itself change the multiplier — it just ensures the Crit
  final-multiplier is applied.

> The "1.25× unless stated otherwise" caveat in the image is referring
> to skills that change the crit multiplier directly (e.g., a few EX
> skills with non-1.25 base crits). Most attacks use the standard
> 1.25× + Crit Damage Up formula above.

## Multi-hit skills

Skills like `1x AoE Sword (200 Power), then 2x random-target Sword
(150 Power each)` apply their attached buff/debuff once per hit.

- **Duration:** stacks per hit. A debuff with a 2-turn duration applied
  3 times to the same target lasts 2+2+2 = 6 turns (subject to the
  game's max-stack limit).
- **Potency:** does **not** stack across the hits of a single skill cast.
  A 15% Sword Res Down hit 3 times remains a 15% Sword Res Down on the
  enemy — but it's now a 6-turn debuff.

This mirrors the "same skill from same unit" rule in `README.md`: each
hit is the same skill from the same unit.

## Defensive math (damage taken)

Defensive buffs (PDEF Up, MDEF Up, Sword Res Up on ally, Fire Res Up on
ally, etc.) follow the **same bucket structure** but on the defender
side.

```
attacker_product = G1_atk × G2_atk × G3_atk × G4_atk × G5_atk × G6_atk
defender_product = G1_def × G2_def × G3_def × G4_def × G5_def

damage_taken = base × attacker_product / defender_product   (× final mults)
```

- The defender's G1 holds Def Up / MDef Up sub-buckets (capped 30%).
- The defender's G3 holds Sword Res Up / Fire Res Up / etc. sub-buckets (capped 30%).
- Same additivity rules within G1/G3 on the defender side.
- G6 (Divine Beast) does not apply on the defender side.

> Some older sources describe defenses as flat % subtraction (e.g.,
> "30% PDEF Up = take 30% less damage"). That framing is consistent
> with the bucket math when it's the only defensive buff, but
> diverges once multiple defensive sources stack — use the bucket model.

## Same-unit-different-ability nuance

> "Buffs and debuffs from different abilities can stack." — meowdb

Two different skills from the **same unit** stack potency, just like
two skills from different units. The "same skill from same unit
extends duration only" rule applies *only* to literally the same
skill being recast.

- **Same group, same sub-bucket** → potency sums, sub-bucket caps at 30%.
  - e.g., Lynette EX (20% Active Atk Up) + Lynette battle skill (20%
    Active Atk Up) → 40% in shared sub-bucket → caps to 30%.
- **Different groups** (e.g., Lynette EX in G1 + Lynette ult in G4) →
  each contributes its own multiplier; no shared cap. *(See example 6
  in `examples.md`.)*

## Boost-extended turn duration

Some skills cap their max duration at, e.g., 5 turns regardless of
re-casts. When duration would exceed the game-imposed maximum, the
extra turns are clipped. (The exact cap differs per skill — check the
description.)

## Open questions / TBD

These are items the user and I have not yet locked down to the level
of formula precision. Treat the rules in `README.md` as authoritative
for the common case and add notes here if a specific scenario reveals
a gap.

- **Soul Potency Up sources.** Which souls grant which potency-up effects, at what %, and against which skill kinds.
- **Skill Potency Up wording.** Exact in-game phrasing of skills that contribute to the Skill Potency final multiplier (vs DMG Up phrasing).
- **Pet Res Down.** No live pet ability provides this at time of writing; the slot is theoretical per the diagram.
- **G1 Crit chance Up sub-bucketing.** Confirmed Crit chance Up is a G1 stat with sub-bucket caps; specific % values across the live roster have not been catalogued here.
- **Hell-weapon 200% cap mechanics.** Confirmed the cap exists; the exact stacking rules for multiple Hell-weapon-equipped allies have not been worked out in this doc.
- **Status ailment resistance 100% cap.** Confirmed; specific applications (e.g., Sleep resistance vs Blind resistance — separate sub-buckets or shared?) not catalogued here.

When any of these gets resolved, update `README.md` (canonical model) or
add a worked example to `examples.md`.
