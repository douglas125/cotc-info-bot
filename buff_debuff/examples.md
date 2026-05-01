# Worked examples

Each example walks the math step-by-step using the rules in `README.md`.
Skill texts in quotes are paraphrased from the user's reference and from
real fixtures in `tests/test_parsers.py` and `tests/test_bot_embeds.py`.

A reminder of the formula:

```
damage = base
       × G1 × G2 × G3 × G4 × G5 × G6
       × Crit × HellHeavenLW × SoulPotency × SkillPotency
```

---

## 1. Lynette + Richard on a Sword attack

**Setup.** Sword attacker attacks a regular enemy. Buffs in play:

| Source | Skill text (paraphrased) | Where it lands |
|--------|--------------------------|----------------|
| Lynette | `Frontrow 20% Atk/Mag Up for 2-5 turns based on Boost Lv` | Active Atk Up (G1), Active Mag Up (G1) |
| Richard active | `Frontrow 20% Atk/Sword Dmg/Spear Dmg Up for 2-5 turns based on Boost Lv` | Active Atk Up (G1), Active Sword DMG Up (G2), Active Spear DMG Up (G2) |
| Richard passive | `While at Full HP, Frontrow 15% Atk Up and 15% Sword/Spear Dmg Up` | Passive Atk Up (G1), Passive Sword DMG Up (G2), Passive Spear DMG Up (G2) |

The attack is **Sword**, so Mag Up and Spear DMG Up don't contribute to
this hit. (Mag Up boosts elemental attacks; Spear DMG Up only applies on
Spear attacks.)

**G1 — Stats**

| Sub-bucket | Sources | Sum | After 30% cap |
|---|---|---|---|
| Active Atk Up | Lynette 20% + Richard 20% | 40% | 30% |
| Passive Atk Up | Richard 15% | 15% | 15% |

```
G1 = 1 + 0.30 + 0.15 = 1.45
```

**G2 — DMG Up**

| Sub-bucket | Sources | Sum | After 30% cap |
|---|---|---|---|
| Active Sword DMG Up | Richard 20% | 20% | 20% |
| Passive Sword DMG Up | Richard 15% | 15% | 15% |

```
G2 = 1 + 0.20 + 0.15 = 1.35
```

**G3 = G4 = G5 = G6 = 1** (no other buffs).

**Final.** Crit / Hell-Heaven-LW / Soul Potency / Skill Potency all 1.

```
damage = base × 1.45 × 1.35 = base × 1.9575
```

Roughly **+96% damage** from these three skills combined on a Sword attack.

---

## 2. Kilns Lv20 ultimate stacked with active and passive Bow buffs

**Setup.** Kilns is a Bow user. He fires off his Lv20 ultimate while a
support has provided a generic Active Atk Up and another character has a
Passive Bow DMG Up.

| Source | Skill text (paraphrased) | Where it lands |
|--------|--------------------------|----------------|
| Kilns ult Lv20 | `30% physical attack up + 30% bow damage up (ultimate)` | Ult Atk Up (G4 Stats), Ult Bow DMG Up (G4 DMG) |
| Other support active | `All allies 20% Atk Up for 3 turns` | Active Atk Up (G1) |
| Other passive | `Frontrow 15% Bow Damage Up` | Passive Bow DMG Up (G2) |

**G1**

| Sub-bucket | Sum | After cap |
|---|---|---|
| Active Atk Up | 20% | 20% |

```
G1 = 1 + 0.20 = 1.20
```

**G2**

| Sub-bucket | Sum | After cap |
|---|---|---|
| Passive Bow DMG Up | 15% | 15% |

```
G2 = 1 + 0.15 = 1.15
```

**G4 — Ultimate (three multiplying sub-pools)**

| Sub-pool | Sub-bucket | Sum | After cap |
|---|---|---|---|
| Stats | Ult Atk Up | 30% | 30% |
| DMG Up | Ult Bow DMG Up | 30% | 30% |
| Res Down | (none) | 0% | 0% |

```
G4_stats   = 1 + 0.30 = 1.30
G4_dmg     = 1 + 0.30 = 1.30
G4_resdown = 1 + 0.00 = 1.00
G4         = 1.30 × 1.30 × 1.00 = 1.69
```

**G3 = G5 = G6 = 1.**

```
damage = base × 1.20 × 1.15 × 1.69 = base × 2.33...
```

Roughly **+133%** before any crit / soul / potency.

---

## 3. Mydia umbrella ultimate (umbrella expansion)

**Setup.** Mydia uses her ultimate, which provides an umbrella physical
buff. Another ult-providing character has +15% Sword DMG Up (ultimate
source). We compute both a Sword attack and an Axe attack to show how the
umbrella spreads.

| Source | Skill text (paraphrased) | Where it lands |
|--------|--------------------------|----------------|
| Mydia ult | `20% physical attack up + 20% physical damage up (ultimate)` | Ult Atk Up (G4 Stats); Ult Phys DMG Up = umbrella adding 20% to **each** of Ult Sword/Dagger/Bow/Axe/Staff/Tome/Fan/Spear DMG Up sub-buckets |
| Other ult | `15% Sword Damage Up (ultimate)` | Ult Sword DMG Up (G4 DMG) |

### 3a. Sword attack

**G4 Stats:**

| Sub-bucket | Sum | After cap |
|---|---|---|
| Ult Atk Up | 20% | 20% |

→ `G4_stats = 1.20`

**G4 DMG Up:** Mydia's umbrella adds 20% to the Ult Sword DMG Up
sub-bucket; the other ult adds 15% to the same sub-bucket. They sum:

| Sub-bucket | Sum | After cap |
|---|---|---|
| Ult Sword DMG Up | 20% + 15% = 35% | **30%** |

→ `G4_dmg = 1.30`. (We lost 5% to the cap.)

→ `G4 = 1.20 × 1.30 × 1.00 = 1.56`

### 3b. Axe attack (only the umbrella applies)

**G4 Stats:** same → `1.20`.

**G4 DMG Up:**

| Sub-bucket | Sum | After cap |
|---|---|---|
| Ult Axe DMG Up | 20% (umbrella alone) | 20% |

→ `G4_dmg = 1.20`. The 15% Sword-specific bonus does **not** help an
Axe attack.

→ `G4 = 1.20 × 1.20 × 1.00 = 1.44`

### 3c. Fire (elemental) attack

The umbrella is **Physical** Damage Up, so it does not touch any of the
6 element sub-buckets.

→ `G4_dmg = 1.00` for a Fire attack.

> **Takeaway.** Umbrella buffs spread independently across every
> per-type sub-bucket they cover, but each sub-bucket caps at 30% on its
> own.

---

## 4. Multi-source same-name buff (different units)

Lynette and Richard each apply their **active** 20% Atk Up to the same
ally (this is rule 2 from the README: different units → potency stacks).

| Sub-bucket | Sources | Sum | After cap |
|---|---|---|---|
| Active Atk Up | Lynette 20% + Richard 20% | 40% | **30%** |

The duration is the longest of the two. The realized boost is 30%, not
40% — 10% is lost to the cap.

```
G1 = 1.30
```

---

## 5. Same skill from same unit reapplied

Lynette casts her 20% Atk Up at turn 1, then casts the **same skill**
again at turn 3 (rule 1: duration extends, potency does not stack).

| Sub-bucket | Sources | Sum | After cap |
|---|---|---|---|
| Active Atk Up | Lynette 20% (single source) | 20% | 20% |

```
G1 = 1.20    (unchanged from a single cast — only duration is longer)
```

---

## 6. Same unit, different skills (different groups can both contribute)

Lynette has an EX skill that grants `20% Atk Up` (Active, **G1**) and her
ultimate also grants `20% Atk Up` (Ultimate, **G4**).

These are in **different groups**. They each contribute their own
multiplier; neither caps the other.

```
G1 = 1 + 0.20 = 1.20      (Active Atk Up sub-bucket)
G4_stats = 1 + 0.20 = 1.20  (Ult Atk Up sub-bucket)
```

Group product picks up `1.20 × 1.20 = 1.44` from these two sources alone.

> Contrast with example 4: there, both 20% Atk Up sources were in the
> *same* G1 sub-bucket, so they summed and capped. Here, the group
> separation shields them from each other.

---

## 7. Sword skill exploiting a Fire weakness

The attacker uses a Sword skill on a Fire-weak enemy; the skill's damage
is dealt as Fire (an exploit). Per rule 7, **the original weapon/element
determines which buffs apply**.

Buffs in play:

| Source | Sub-bucket | Applies? |
|---|---|---|
| Active 20% Sword DMG Up | G2 Active Sword DMG Up | ✓ |
| Active 20% Fire DMG Up | G2 Active Fire DMG Up | ✗ (original is Sword) |
| Active 20% Sword Res Down on enemy | G3 Active Sword Res Down | ✓ |
| Active 20% Fire Res Down on enemy | G3 Active Fire Res Down | ✗ |

```
G2 = 1 + 0.20 = 1.20
G3 = 1 + 0.20 = 1.20
```

The four entries stack down to two effective contributions because the
Fire-typed buffs/debuffs do not apply when the *original* skill type is
Sword.

---

## 8. End-to-end damage walk

A maximally stacked Bow attack against a Hell-aligned enemy. Every layer
of the formula contributes.

| Source | % | Where it lands |
|---|---|---|
| Active Atk Up | 25% | G1 Active Atk Up |
| Passive Atk Up | 15% | G1 Passive Atk Up |
| Active Bow DMG Up | 20% | G2 Active Bow DMG Up |
| Passive Bow DMG Up | 10% | G2 Passive Bow DMG Up |
| Active Bow Res Down on enemy | 25% | G3 Active Bow Res Down |
| Kilns Lv20 ult: Ult Atk Up | 30% | G4 Stats |
| Kilns Lv20 ult: Ult Bow DMG Up | 30% | G4 DMG |
| Pet ability: Pet Atk Up | 15% | G5 Stats |
| Divine Beast active | 10% | G6 |
| Crit + Crit Damage Up | 1.25 + 30% | Final multiplier (Crit) |
| Hell weapon vs Hell enemy | 50% | Final multiplier (HellHeavenLW) |
| Soul Potency Up | 20% | Final multiplier (SoulPotency) |
| Skill Potency Up | 15% | Final multiplier (SkillPotency) |

**Group products.**

```
G1 = 1 + 0.25 + 0.15                           = 1.40
G2 = 1 + 0.20 + 0.10                           = 1.30
G3 = 1 + 0.25                                  = 1.25
G4 = (1 + 0.30) × (1 + 0.30) × 1               = 1.30 × 1.30 = 1.69
G5 = (1 + 0.15) × 1 × 1                        = 1.15
G6 = 1.10

group_product = 1.40 × 1.30 × 1.25 × 1.69 × 1.15 × 1.10
             ≈ 4.864
```

**Final multipliers.**

```
Crit          = 1.25 + 0.30 = 1.55
HellHeavenLW  = 1 + 0.50    = 1.50      (well under the 200% Hell cap)
SoulPotency   = 1 + 0.20    = 1.20
SkillPotency  = 1 + 0.15    = 1.15
```

**Damage.**

```
damage = base × 4.864 × 1.55 × 1.50 × 1.20 × 1.15
       ≈ base × 15.6
```

Roughly **15.6×** the base damage of a no-buff hit. This is why stacking
matters in COTC — each layer is multiplicative, not additive.

---

For nuances not covered here (Boost Lv scaling, special-case caps, crit
mechanics, defensive math), see `edge_cases.md`.
