# Team composition mechanics

The bucket math in `README.md` describes how *one attack* resolves
into damage. This document covers the team-level mechanics that
determine *which characters* participate, *when* their buffs are
active, and *how often* skills fire. These are inputs to a team
analyser (e.g. `/analyze_team`) but not part of the per-attack
formula.

## Frontrow + backrow

A CotC team is **8 characters**: 4 in the **frontrow** and 4 in the
**backrow**. Both rows are *active* — every character contributes
their passive effects to the team simultaneously, and any character
can be swapped between rows mid-battle.

- A character's **passive skills fire continuously** while their
  prerequisites are met, **regardless of which row they are in**, with
  one exception (see "row-gated passives" below).
- A character's **active skills can only be used while in the
  frontrow.** This is what makes row swapping a tactical decision
  rather than a free choice.
- Damage dealers therefore live in the frontrow during the **break
  window** (when actives are firing). During the **preparation turns**
  before a break, supports can move to the front to apply buffs /
  debuffs and the damage dealers move to the back to wait.

**For team-analysis purposes:** all 8 characters' classified effects
are pooled into the offensive bucket math and the survivability
verdict. Row position is recorded for display, but does not gate
contributions unless a passive explicitly says it does.

### Row-gated passives

Some passives are written to fire **only** while the character is in
the frontrow. The skill description says so explicitly. **Kilns's**
passive is the canonical example.

The default is *fires anywhere*. **Bargello's** passive — buffs to
physical and elemental attack plus damage cap up while a broken
enemy is present — fires regardless of which row he's in, and this
is the common case.

A team analyser should treat row-gating as opt-in: classified
effects carry a `requires_frontrow` flag set only when the
description says so.

## The break window

Every encounter has a **break shield count** per enemy member.
Hitting weakness icons depletes the shields; when they reach zero
the enemy enters **break**.

- Standard break duration is **2 turns**. Most teams stack their
  damage into this 2-turn window because non-broken enemies take
  greatly reduced damage.
- **Nier** is the only character (at time of writing) whose effect
  extends break to **3 turns**. Teams with Nier get an extra cycle
  of damage-window output.

For team analysis, break is a timing assumption rather than a
multiplier — the analyser reports buffs/debuffs and damage potential
**as if** the damage window is open. If the team can't reliably reach
break, that's a different deficiency surfaced separately (and not
modelled in the v1 of `/analyze_team`).

## Auto-revive is **not** survivability

Several skills grant auto-revive (Black Maiden's passive, Black
Maiden's ult, certain status effects like "Necromance"). These are
**not** treated as a survivability tier by the analyser, because in
CotC, when a unit dies they lose all their applied buffs. Reviving
them brings them back at HP but without the Atk/Mag/cap-up/etc.
buffs that were sustaining the team's damage — so the resurrection
doesn't preserve combat continuity in the way regen / undying do.

Auto-revive skills should not be classified into the survivability
categories (``regen`` / ``heal`` / ``undying``). They may still
appear as informational text in the embed (e.g. as flavour in the
team header), but they don't change the tier verdict.

The categories that *do* count for survivability are, in priority
order:

1. **Undying** — Shana's mechanic, applied via her EX SP-consume
   path: ``60% SP consumed: "Undying" for 2 turns``.
2. **Full-party regen** — any active member's regen with
   ``target_scope`` in ``{'all_allies', 'other_allies'}``.
   Examples: EX Primrose's active at Boost MAX
   (``If Boost MAX, targets All Allies instead``), EX Molrusso EX
   ``All Allies Regen ... for 2-5 turns``, Rinyuu's channelled
   "Prayer of Grace".
3. **Frontrow regen** — ``target_scope='frontrow'``.
4. **Heal-only** — one-shot heals, no regen ticks.
5. **None**.

## Damage-relevant skills require ≥4 effective hits

A skill is **damage-relevant** for the per-DPS summary only when
its **effective hit count** is at least 4. Effective hits is
``listed_hits × self_multi_cast_factor``, so:

- A 5-hit skill on a non-multi-cast unit qualifies (5 ≥ 4).
- A 2-hit skill on a triplecasting unit qualifies (2 × 3 = 6 ≥ 4).
- A 1-hit skill on a triplecasting unit does **not** qualify
  (1 × 3 = 3 < 4).
- A 3-hit skill at default multi-cast does **not** qualify.

The reason is the per-hit damage cap: with cap-up sources stacking
to ~+200k–+300k on a typical Good-tier team, each capped hit deals
~200k–~300k. 3 hits × 250k = 750k ceiling; 8 hits × 250k = 2M
ceiling — the gap is large enough that low-hit-count skills aren't
worth highlighting as the team's primary damage source even when
their per-hit power is huge. (1x 750-power skills like
Pardis's `1x single-target Sword (1x 530-900 Power)` still exist;
they're just not picked as "best damage skill" for the auto-DPS
table.)

## Multi-cast (doublecast / triplecast / …)

A **multi-cast** effect causes the next skill cast to **fire more
than once**, multiplying its hit count.

- *Doublecast*: skill resolves 2× → effective hits = listed hits × 2.
- *Triplecast*: skill resolves 3× → effective hits = listed hits × 3.

Multi-cast is typically **self-scoped** — granted to the caster
themselves — and comes from active skills, passives, or ultimates.
Examples seen in good sword teams:

- **Pardis III** — his ultimate grants self-doublecast.
- **Dark Knight** — has self-triplecast.

Because per-hit damage is independently capped, multi-cast directly
converts into more hits-at-cap, so it's a strong damage multiplier
once cap is met. A team analyser should:

1. Classify multi-cast as its own effect category, with magnitude
   equal to the multi-cast factor (`2.0`, `3.0`, …).
2. Apply it per-DPS: only the DPS whose target the multi-cast
   benefits gets the hit-count multiplier. (Self-scoped multi-cast
   means only the originating character benefits when they ARE the
   DPS.)
3. Total damage potential per turn ≈
   `effective_hits × min(buff_multiplier × per_hit_potency, per_hit_cap)`,
   where `effective_hits = listed_hits × multi_cast_factor`.

## Follow-up attacks

Some skills/passives cause an *additional* attack to fire after an
ally's action. Two flavours seen so far:

1. **Self follow-up** — *the originating character* performs the
   extra hit. Example: Xerc's passive
   ``While in frontrow, Xerc will Follow-Up any Elemental Attack
   with a 1 hit AoE of the same element up to once per element per
   action (1x 50 Power)`` — if a frontrow ally exploits 6
   elemental weaknesses in one cast, Xerc fires 6 follow-ups.
2. **Ally follow-up** — *the original attacker* performs the extra
   hit. Example: base Osvald's passive
   ``While in Front Row, after a Frontrow Ally uses a Fire, Ice or
   Lightning ability, that Ally performs a 1x random-target attack
   of the same element (1x 230 Power)`` — adds up to 3 hits per
   ally action.

Follow-up attacks are independent attacks, so they each go through
the bucket math and the per-hit cap on their own. They benefit from
the team's buff stack like any other attack of their weapon/element
type.

A team analyser should treat follow-ups as an additive hit count
on the *attacker*, not as a buff. Phase 2 patterns will need to
classify them with a category like ``follow_up_attack`` along with
the per-element trigger count.

## Single-target potency-up bridges

Some ultimates grant **skill potency up** to a single ally rather
than the whole party. Known sources:

- **Solon** ultimate
- **Molrusso EX** ultimate
- **Rinyuu EX** ultimate

These are tactically valuable as **bridges**: a self-potency-up
buff on a damage dealer typically lasts only 2 turns, and the cast
itself consumes 1 turn, leaving 1 effective turn of buffed attacks.
A single-target potency-up from a teammate in the following turn
extends the damage dealer's buffed window across the full 2-3 turn
break.

A team analyser should:

1. Classify these with `target_scope='self'` (or a more general
   "single ally") so they don't double-count for non-DPS teammates.
2. Add their magnitude to a candidate DPS's potency only when the
   recipient is that DPS. (For analysis purposes the v1 simplifying
   assumption is that they go to the team's identified DPS — phase 2
   may make this configurable.)

## Observed wording (sword-team example)

These are literal phrasings the classifier patterns must recognise.
Drawn from the live skill text for Black Knight, Pardis, EX Pardis,
and EX Molrusso — keep this list growing as more team examples are
audited.

| Mechanic | Literal phrasing seen |
|---|---|
| Skill Potency Up | `100% Potency Up`, `150% Potency Up` |
| Damage cap up on team | `+100,000 Damage Cap`, `100000 Damage Cap Up`, `+50,000 Damage Cap`, `+10,000 Damage Cap` |
| Damage cap up — single-target | `Single ally 100% Potency Up ... +100,000 Damage Cap for 1 turn` (EX Molrusso ult) |
| Self damage cap up | `Self ... +100,000 Damage Cap for 3-6 turns` (Black Knight EX) |
| Skill-level conditional cap up | `This attack has 100000 Damage Cap Up while there is a Broken enemy` (Pardis active) |
| Self triplecast (modern) | `grant self triplecast for the next 2 offensive skills` (Black Knight ult, EX Pardis EX) |
| Self doublecast (older wording) | `attack abilities are used a second time in a row for 3 turns` (Pardis ult) |
| Multi-cast count-limited | `for the next 2 offensive skills` — multi-cast can be skill-count-limited, not just turn-duration |
| Sub-bucket cap raise to 50% | `Increase buff limit for Atk and Sword Damage Up for Active Skills to 50% on self for 3-6 turns` (Black Knight EX) |
| Guaranteed crit | `Self Guaranteed Crit` (Pardis EX) |
| Stat buffs on a status effect | `"Cursed State": 10% Atk/Def/Crit Up` (Black Knight passive) |
| Conditional team-state buff | `Grant self benefits based on the amount of allies in "Cursed State": 1+ ally: 30% Atk Up and Sword Damage Up` (Black Knight tp passive) |
| Status-bound buff via ult | `Also grant "Legend" for 4 turns` (EX Molrusso ult) |
| Frontrow-gated passive | `While in the frontrow and at the end of the turn: ...` (Black Knight passive) |
| All-allies regen | `All Allies Regen ... for 2-5 turns ... (140 Regen Strength)` (EX Molrusso EX) |
| Frontrow regen | `Frontrow Regen for 2-5 turns ... (120 Regen Strength)` (Shana active) |
| Undying tag | `60% SP consumed: "Undying" for 2 turns` (Shana EX) |

## Sub-bucket cap-raise — beyond the 30% default

`README.md` states the default sub-bucket cap is 30%. Some abilities
explicitly raise that cap on themselves or their target. Black
Knight's EX raises Self's cap on `Atk Up` and `Sword Damage Up` for
active skills to 50% for 3-6 turns. While such an effect is active,
the affected sub-buckets cap at 50% instead of 30% — the
``damage/types.py::JP_OVERRIDE_CAP`` constant exists for exactly this.

A team analyser should:

1. Classify the cap-raise effect with its own marker (e.g.
   ``ClassifiedEffect.category='cap_raise'``) carrying the affected
   sub-buckets and the new cap (e.g. 0.50).
2. When running the bucket math for a specific DPS, override the
   default cap on those sub-buckets per-DPS-and-source-kind, again
   honouring `target_scope` so a "self" cap-raise only helps the
   originating character.

## Status effects that contain buffs

Some status effects are themselves containers for stat buffs. Two
seen so far:

- **Cursed State** (Black Knight): *"10% Atk/Def/Crit Up"*. Black
  Knight's passive grants Cursed State to revived allies, and his
  TP passive scales his own buffs by the number of allies who have
  it. So the "buffs" line is *contingent on the Cursed-State count*.
- **Legend** (EX Molrusso ultimate): grants the target "Legend" for
  4 turns alongside the potency-up. The buffs Legend implies have
  not been catalogued here yet — open question.

Status-contained buffs are tricky for a classifier because the
buff text isn't on the line that grants the status. The current
plan is to:

1. Treat the *status grant* as the source effect (classified with the
   relevant buffs and a duration).
2. Add a TODO list of statuses whose buff content needs explicit
   text — Cursed State, Legend, others — to the open questions in
   `edge_cases.md` so they don't slip.

## Turn-cycle awareness (deferred)

CotC damage is fundamentally a turn-cycle problem: which buffs are
up *together*, how long their windows overlap, and how those
windows align with the break window. The v1 `/analyze_team` does
**not** simulate turns — it assumes every classified buff is active
simultaneously during the damage window, which is the *ceiling* of
the team's potential. The embed footer should note this assumption.

Future iterations may add a turn-by-turn projection that highlights
gaps (e.g. "Dark Knight's potency window collapses without
Molrusso EX's bridge") but the bucket math is the same in either
case — the cycle simulation only affects which subset of buffs
counts at any given turn.
