"""Audit CLI: ``python -m analysis.audit "Name1, Name2, ..."``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from db import repo

from . import aggregator, coverage, damage_estimate, insights, resolve, survivability
from .types import AssumptionProfile


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    db_path = Path(args.db) if args.db else None
    conn = repo.connect(db_path)

    front = _split_names(args.frontrow)
    back = _split_names(args.backrow) if args.backrow else []

    front_resolved = _resolve_inputs(conn, "frontrow", front)
    back_resolved = _resolve_inputs(conn, "backrow", back)
    unresolved = [r for r in front_resolved + back_resolved if r[2] is None]
    front_ids = [r[2] for r in front_resolved if r[2] is not None]
    back_ids = [r[2] for r in back_resolved if r[2] is not None]

    if not front_ids:
        _print_unresolved(unresolved)
        print("no frontrow members resolved; cannot analyze team", file=sys.stderr)
        return 2

    profile = AssumptionProfile(boost_level=args.boost)
    bucketed = aggregator.aggregate_team(
        conn,
        frontrow_form_ids=front_ids,
        backrow_form_ids=back_ids,
        cap_orbs=args.cap_orbs,
        divine_beast=args.divine_beast,
        profile=profile,
    )
    verdict = survivability.assess(bucketed, conn)
    matrix = coverage.build(bucketed)
    damage = damage_estimate.build(bucketed, conn)

    _print_report(
        conn=conn,
        bucketed=bucketed,
        verdict=verdict,
        matrix=matrix,
        damage=damage,
        unresolved=unresolved,
        debug=args.debug,
    )
    return 1 if (args.strict and bucketed.unparsed) else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m analysis.audit",
        description="Run the team analysis pipeline against the live DB.",
    )
    p.add_argument("frontrow", help='Comma-separated frontrow 1-4 (e.g. "Shana,Primrose EX,...").')
    p.add_argument("--backrow", default="", help="Comma-separated backrow 0-4.")
    p.add_argument("--boost", type=int, default=3, choices=[0, 1, 2, 3])
    p.add_argument("--cap-orbs", type=int, default=0, choices=[0, 1, 2, 3])
    p.add_argument("--divine-beast", action="store_true")
    p.add_argument("--debug", action="store_true",
                   help="Print raw coverage, per-DPS details, and unparsed descriptions.")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero when any skill is unparsed.")
    p.add_argument("--db", default=None, help="Override DB path (defaults to config.DB_PATH).")
    return p.parse_args(argv)


def _split_names(raw: str) -> list[str]:
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


def _resolve_inputs(conn, row_name: str, names: list[str]) -> list[tuple[str, str, int | None, list[str]]]:
    out: list[tuple[str, str, int | None, list[str]]] = []
    for name in names:
        fid = resolve.resolve_form_id(conn, name)
        suggestions = [] if fid is not None else resolve.suggest_names(conn, name)
        out.append((row_name, name, fid, suggestions))
    return out


def _print_unresolved(unresolved) -> None:
    for row_name, name, _fid, suggestions in unresolved:
        hint = f" suggestions: {', '.join(suggestions)}" if suggestions else " no close DB match"
        print(f"unresolved {row_name}: {name};{hint}", file=sys.stderr)


def _print_report(*, conn, bucketed, verdict, matrix, damage, unresolved, debug: bool) -> None:
    print("=" * 72)
    print("Team analysis")
    print("=" * 72)
    print("Inputs:")
    print(f"  Frontrow: {_name_line(conn, bucketed.frontrow_form_ids)}")
    print(f"  Backrow:  {_name_line(conn, bucketed.backrow_form_ids)}")
    effective_orbs = _effective_orb_count(bucketed)
    print(
        f"  Free cap orbs: {bucketed.cap_orbs} entered, {effective_orbs} counted "
        f"(+{effective_orbs * 100_000:,.0f}); max one per character"
    )
    print(f"  Divine Beast: {bucketed.divine_beast}")
    print(f"  Profile: boost={bucketed.profile.boost_level}, "
          f"full_hp={bucketed.profile.assume_full_hp}, "
          f"channeling={bucketed.profile.assume_channeling_active}")
    print()

    if unresolved:
        print("Unresolved names:")
        for row_name, name, _fid, suggestions in unresolved:
            hint = f" suggestions: {', '.join(suggestions)}" if suggestions else " no close DB match"
            print(f"  - {row_name}: {name};{hint}")
        print("  These are excluded from the analysis; the local DB may be stale.")
        print()

    ranked = insights.ranked_dps(bucketed, damage, limit=3)
    print("Best use of this team:")
    if ranked:
        for i, dps in enumerate(ranked, start=1):
            print(f"  {i}. {insights.format_dps_line(dps)}")
    else:
        print("  No parsed primary DPS candidate.")
    print()

    print("Main gaps:")
    gaps = []
    if unresolved:
        gaps.append(
            f"{len(unresolved)} submitted member(s) were unresolved and excluded; refresh/sync or add aliases before trusting full-team output."
        )
    gaps.extend(insights.gap_lines(bucketed, damage, ranked))
    if gaps:
        for line in gaps:
            print(f"  - {line}")
    else:
        print("  - No major gap found by the current parser.")
    print()

    print(f"Survivability: {verdict.tier} ({_ascii(verdict.primary_source_display)})")
    for c in verdict.citations[:3]:
        print(f"  - {_ascii(c.snippet)}")
    print()

    print("Team cap and potency:")
    print(f"  Team-wide cap up: +{damage.team_damage_cap_up:,.0f} ({damage.cap_tier})")
    print("  Free +100k cap orbs are one per character; other cap must come from A4/accessory/skill effects.")
    print(f"  Team skill potency: +{damage.team_skill_potency_up * 100:.0f}%")
    print(f"  Team soul potency:  +{damage.team_soul_potency_up * 100:.0f}%")
    print()

    support = insights.support_summaries(bucketed, damage, exclude=[d.summary.form_id for d in ranked])
    if support:
        print("Support roles:")
        for line in support[:6]:
            print(f"  - {line}")
        print()

    print(f"Parser coverage: {len(bucketed.classified)} classified, {len(bucketed.unparsed)} unparsed")
    if bucketed.unparsed and not debug:
        print("  Run with --debug to inspect unparsed skill text.")

    if debug:
        _print_debug(bucketed=bucketed, matrix=matrix, damage=damage)


def _print_debug(*, bucketed, matrix, damage) -> None:
    print()
    print("Raw coverage matrix:")
    if coverage.is_empty(matrix):
        print("  (empty)")
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
                    print(f"    {_pretty_bucket(k)}  +{v * 100:.1f}%")
        print("  Final multipliers by type:")
        print("    Weapons:  " + _type_multiplier_line(
            bucketed, ("sword", "dagger", "bow", "axe", "staff", "tome", "fan", "spear")
        ))
        print("    Elements: " + _type_multiplier_line(
            bucketed, ("fire", "ice", "lightning", "wind", "light", "dark")
        ))
    print()

    print("Raw per-DPS damage:")
    for dps in damage.per_dps:
        team_wide, self_only = damage_estimate.cap_up_breakdown_for_dps(bucketed, dps.form_id)
        team_pot, self_pot = damage_estimate.potency_up_breakdown_for_dps(bucketed, dps.form_id)
        multi_cast = damage_estimate.self_multi_cast_factor(bucketed, dps.form_id)
        print(f"  - {dps.display_name} ({dps.weapon or '?'}/{dps.element or '?'})")
        print(f"      buff multiplier: x{dps.buff_multiplier:.3f}")
        print(f"      cap up team:     {team_wide:,.0f}")
        print(f"      cap up self:     {self_only:,.0f}")
        print(f"      potency team:    +{team_pot * 100:.0f}%")
        print(f"      potency self:    +{self_pot * 100:.0f}%")
        print(f"      self multi-cast: x{multi_cast:.0f}")
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

    if bucketed.unparsed:
        print()
        print("Unparsed descriptions:")
        for u in bucketed.unparsed:
            print(f"  - form={u.source_form_id} skill={u.source_skill_id} "
                  f"({u.source_kind}): {_ascii(u.raw_description[:160])}")


def _type_multiplier_line(bucketed, types: tuple[str, ...]) -> str:
    return ", ".join(
        f"{t.title()} x{damage_estimate.final_multiplier_for_type(bucketed, t):.2f}"
        for t in types
    )


def _name_line(conn, form_ids) -> str:
    names: list[str] = []
    for fid in form_ids:
        row = repo.get_form(conn, fid)
        names.append(row["display_name"] if row else f"form#{fid}")
    return ", ".join(names) if names else "-"


def _effective_orb_count(bucketed) -> int:
    return max(0, min(bucketed.cap_orbs, 3, len(bucketed.all_form_ids)))


def _pretty_bucket(key: str) -> str:
    parts = key.split(".")
    if len(parts) != 3:
        return key
    group, source, tail = parts
    label = tail.replace("_", " ")
    return f"{group.upper()} {source} {label}"


def _ascii(value: str) -> str:
    return (value or "-").encode("ascii", "replace").decode("ascii")


if __name__ == "__main__":
    sys.exit(main())
