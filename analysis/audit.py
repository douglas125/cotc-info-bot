"""Audit CLI: ``python -m analysis.audit "Name1, Name2, ..."``.

Resolves a comma-separated list of names through the live DB, runs the
full analysis pipeline, and prints a debug-friendly text report. This
is the primary tool the user runs to validate classifier patterns before
the slash command goes live — fast iteration loop, no Discord round-
trip.

Phase 1 ships with an empty pattern table; the audit prints the expected
"unparsed: N" counts and "Survivability: None" verdict. As Phase 2
patterns are added, the report's verdict and coverage matrix populate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from damage.types import ELEMENTS, WEAPONS
from db import repo

from . import aggregator, coverage, damage_estimate, resolve, survivability
from .types import AssumptionProfile


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    db_path = Path(args.db) if args.db else None
    conn = repo.connect(db_path)

    front = _split_names(args.frontrow)
    back = _split_names(args.backrow) if args.backrow else []

    front_ids = resolve.resolve_form_ids(conn, front)
    if any(fid is None for fid in front_ids):
        unresolved = [n for n, fid in zip(front, front_ids) if fid is None]
        print(f"unresolved frontrow: {unresolved}", file=sys.stderr)
        return 2
    back_ids = [fid for fid in resolve.resolve_form_ids(conn, back) if fid]

    profile = AssumptionProfile(boost_level=args.boost)
    bucketed = aggregator.aggregate_team(
        conn,
        frontrow_form_ids=[fid for fid in front_ids if fid is not None],
        backrow_form_ids=back_ids,
        cap_orbs=args.cap_orbs,
        divine_beast=args.divine_beast,
        profile=profile,
    )
    verdict = survivability.assess(bucketed, conn)
    matrix = coverage.build(bucketed)
    damage = damage_estimate.build(bucketed, conn)

    _print_report(
        bucketed=bucketed, verdict=verdict, matrix=matrix, damage=damage,
        debug=args.debug,
    )
    return 1 if (args.strict and bucketed.unparsed) else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m analysis.audit",
        description="Run the team analysis pipeline against the live DB.",
    )
    p.add_argument("frontrow", help='Comma-separated frontrow 1-4 (e.g. "Shana,Primrose EX,...").')
    p.add_argument("--backrow", default="", help='Comma-separated backrow 0-4.')
    p.add_argument("--boost", type=int, default=3, choices=[0, 1, 2, 3])
    p.add_argument("--cap-orbs", type=int, default=0, choices=[0, 1, 2, 3])
    p.add_argument("--divine-beast", action="store_true")
    p.add_argument("--debug", action="store_true",
                   help="Print every unparsed skill description.")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero when any skill is unparsed.")
    p.add_argument("--db", default=None, help="Override DB path (defaults to config.DB_PATH).")
    return p.parse_args(argv)


def _split_names(raw: str) -> list[str]:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


def _print_report(*, bucketed, verdict, matrix, damage, debug: bool) -> None:
    print("=" * 72)
    print("Team analysis (Phase 1 scaffold — patterns may be empty)")
    print("=" * 72)
    print(f"Frontrow forms: {list(bucketed.frontrow_form_ids)}")
    print(f"Backrow forms:  {list(bucketed.backrow_form_ids)}")
    print(f"Cap orbs:       {bucketed.cap_orbs} → +{bucketed.cap_orbs * 100_000:,.0f} cap")
    print(f"Divine Beast:   {bucketed.divine_beast}")
    print(f"Profile:        boost={bucketed.profile.boost_level} "
          f"full_hp={bucketed.profile.assume_full_hp} "
          f"channeling={bucketed.profile.assume_channeling_active}")
    print()
    print(f"Survivability: {verdict.tier} ({verdict.primary_source_display})")
    for c in verdict.citations:
        print(f"  - form={c.form_id} skill={c.skill_id}: {c.snippet}")
    print()
    print(f"Cap up total:  {damage.team_damage_cap_up:,.0f}  ({damage.cap_tier})")
    print(f"Skill Potency: +{damage.team_skill_potency_up * 100:.1f}%")
    print(f"Soul Potency:  +{damage.team_soul_potency_up * 100:.1f}%")
    print()
    print("Coverage matrix:")
    if coverage.is_empty(matrix):
        print("  (empty — Phase 1 pattern table is empty)")
    else:
        for label, sub in (
            ("G1 Stats", matrix.g1),
            ("G2 DMG Up", matrix.g2),
            ("G3 Res Down", matrix.g3),
            ("G4 Ultimate", matrix.g4),
            ("G5 Pets", matrix.g5),
        ):
            if sub:
                print(f"  {label}:")
                for k, v in coverage.top_n(sub, n=10):
                    print(f"    {k}  +{v * 100:.1f}%")
        print("  Final multipliers by type:")
        print("    Weapons:  " + _type_multiplier_line(bucketed, WEAPONS))
        print("    Elements: " + _type_multiplier_line(bucketed, ELEMENTS))
    print()
    print("Per-DPS damage:")
    for dps in damage.per_dps:
        team_wide, self_only = damage_estimate.cap_up_breakdown_for_dps(
            bucketed, dps.form_id,
        )
        team_pot, self_pot = damage_estimate.potency_up_breakdown_for_dps(
            bucketed, dps.form_id,
        )
        multi_cast = damage_estimate.self_multi_cast_factor(bucketed, dps.form_id)
        print(f"  - {dps.display_name}  ({dps.weapon or '?'}/{dps.element or '?'})")
        print(f"      buff multiplier: ×{dps.buff_multiplier:.3f}")
        print(f"      cap up team:     {team_wide:,.0f}")
        print(f"      cap up self:     {self_only:,.0f}")
        print(f"      potency team:    +{team_pot * 100:.0f}%")
        print(f"      potency self:    +{self_pot * 100:.0f}%")
        print(f"      self multi-cast: ×{multi_cast:.0f}")
        if not dps.best_skills:
            print("      best skills:     (no skill with effective_hits >= 4)")
        for s in dps.best_skills:
            label = s.name or f"slot-{s.skill_id}"
            potency = damage_estimate.realised_potency(s.power_max, team_pot + self_pot)
            cap_each = damage_estimate.caps_each_hit(
                power=s.power_max,
                skill_potency_up=team_pot + self_pot,
                team_damage_cap_up=team_wide + self_only,
            )
            eff = damage_estimate.effective_hits_for_skill(s, multi_cast)
            print(f"        - [{s.skill_kind}] {label}  "
                  f"power={s.power_min}-{s.power_max} hits={s.hits or '?'}"
                  f"{f' (eff {eff})' if eff != (s.hits or 0) else ''}  "
                  f"realised={potency:.0f} caps_each_hit={cap_each}")
    print()
    print(f"Classified effects: {len(bucketed.classified)}  "
          f"unparsed: {len(bucketed.unparsed)}")
    if debug and bucketed.unparsed:
        print("Unparsed descriptions:")
        for u in bucketed.unparsed:
            print(f"  - form={u.source_form_id} skill={u.source_skill_id} "
                  f"({u.source_kind}): {u.raw_description[:160]}")


def _type_multiplier_line(bucketed, types: tuple[str, ...]) -> str:
    return ", ".join(
        f"{t.title()} x{damage_estimate.final_multiplier_for_type(bucketed, t):.2f}"
        for t in types
    )


if __name__ == "__main__":
    sys.exit(main())
