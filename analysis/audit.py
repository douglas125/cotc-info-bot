"""Audit CLI: ``python -m analysis.audit "Name1, Name2, ..."``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The new line format uses ≈, ×, → glyphs which match the Discord embed.
# Windows consoles default to cp1252, which can't encode them. Reconfigure
# stdout/stderr to UTF-8 (errors='replace') so the CLI never crashes on a
# rendering character.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

from db import repo

from . import aggregator, coverage, damage_estimate, insights, resolve, survivability
from .types import AssumptionProfile, NameResolution


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
    name_resolutions = _build_name_resolutions(
        conn, front_resolved + back_resolved,
    )

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
        name_resolutions=name_resolutions,
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
    p.add_argument(
        "--cap-orbs", type=int, default=0,
        help="Free +100k cap orbs equipped on the team. Values >3 still "
             "accepted; the gap-lines surface the 3-orb stacking rule.",
    )
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


def _print_report(
    *,
    conn,
    bucketed,
    verdict,
    matrix,
    damage,
    unresolved,
    name_resolutions: tuple[NameResolution, ...] = (),
    debug: bool,
) -> None:
    print("=" * 72)
    print("Team analysis")
    print("=" * 72)
    print("Inputs:")
    print(f"  Frontrow: {_name_line(conn, bucketed.frontrow_form_ids)}")
    print(f"  Backrow:  {_name_line(conn, bucketed.backrow_form_ids)}")
    effective_orbs = bucketed.effective_cap_orbs
    print(
        f"  Free cap orbs: {bucketed.cap_orbs} entered, {effective_orbs} counted "
        f"(+{effective_orbs * 100_000:,.0f}); max one per character"
    )
    print(f"  Divine Beast: {bucketed.divine_beast}")
    print(f"  Profile: boost={bucketed.profile.boost_level}, "
          f"full_hp={bucketed.profile.assume_full_hp}, "
          f"channeling={bucketed.profile.assume_channeling_active}")
    aliased = [r for r in name_resolutions if r.is_alias]
    if aliased:
        pairs = ", ".join(f"{r.typed} -> {r.resolved_display_name}" for r in aliased)
        print(f"  Aliased inputs: {pairs}")
    classified, total = insights.parser_confidence(bucketed)
    if total:
        ratio = round((classified / total) * 100)
        print(f"  Parser confidence: {classified}/{total} effects classified ({ratio}%)")
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
            line = insights.format_dps_line(dps)
            head, _, body = line.partition("\n")
            print(f"  {i}. {head}")
            if body:
                print(f"     {body.strip()}")
    else:
        print("  No parsed primary DPS candidate.")
    print()

    print("Damage potential by type:")
    for label, line in _type_matrix_lines(bucketed):
        print(f"  {label}: {line}")
    print()

    print("Main gaps:")
    gaps = []
    if unresolved:
        gaps.append(
            f"{len(unresolved)} submitted member(s) were unresolved and excluded; "
            "refresh/sync or add aliases before trusting full-team output."
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
    orb_contribution = effective_orbs * 100_000
    other_team_cap = max(0.0, damage.team_damage_cap_up - orb_contribution)
    print(
        f"  Team-wide: +{insights._compact(damage.team_damage_cap_up)} ({damage.cap_tier}) "
        f"= {effective_orbs} orb(s) (+{insights._compact(orb_contribution)}) + "
        f"skill/A4 (+{insights._compact(other_team_cap)})"
    )
    self_lines = []
    for fid in bucketed.all_form_ids:
        _team_cap, self_cap = damage_estimate.cap_up_breakdown_for_dps(bucketed, fid)
        if self_cap <= 0:
            continue
        row = repo.get_form(conn, fid)
        name = row["display_name"] if row else f"form#{fid}"
        self_lines.append(f"{name} +{insights._compact(self_cap)}")
    if self_lines:
        print(f"  Self-only cap-up: {', '.join(self_lines)}")
    if damage.team_skill_potency_up:
        print(f"  Team skill potency: +{damage.team_skill_potency_up * 100:.0f}%")
    if damage.team_soul_potency_up:
        print(f"  Team soul potency:  +{damage.team_soul_potency_up * 100:.0f}%")
    print()

    support = insights.support_summaries(bucketed, damage, exclude=[d.summary.form_id for d in ranked])
    if support:
        print("Support roles:")
        for line in support[:6]:
            print(f"  - {_ascii(line)}")
        print()

    print(f"Parser coverage: {len(bucketed.classified)} classified, {len(bucketed.unparsed)} unparsed")
    if bucketed.unparsed and not debug:
        print("  Run with --debug to inspect unparsed skill text.")

    if debug:
        _print_debug(bucketed=bucketed, matrix=matrix, damage=damage)


def _type_matrix_lines(bucketed) -> list[tuple[str, str]]:
    """``[("Weapons", "Sword x6.17 ..."), ("Elements", "Fire x5.29 ...")]``."""
    weapon_pairs = sorted(
        (
            (w, damage_estimate.final_multiplier_for_type(bucketed, w))
            for w in ("sword", "dagger", "bow", "axe", "staff", "tome", "fan", "spear")
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )
    element_pairs = sorted(
        (
            (e, damage_estimate.final_multiplier_for_type(bucketed, e))
            for e in ("fire", "ice", "lightning", "wind", "light", "dark")
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )

    def _line(pairs):
        if not pairs:
            return "-"
        head = " | ".join(f"{n.title()} x{m:.2f}" for n, m in pairs[:3])
        rest = pairs[3:]
        if rest and all(abs(rest[0][1] - other) < 0.01 for _n, other in rest):
            head += f"  (others x{rest[0][1]:.2f})"
        return head

    return [
        ("Weapons", _line(weapon_pairs)),
        ("Elements", _line(element_pairs)),
    ]


def _build_name_resolutions(
    conn, resolved_rows,
) -> tuple[NameResolution, ...]:
    """Convert audit's ``[(row, typed, fid, suggestions), ...]`` into
    :class:`NameResolution` entries.

    Resolution kind is inferred from the typed input vs. the resolved
    display name: identical (case-insensitive) → ``exact``, different →
    ``alias`` (covers both ``config.NAME_ALIASES`` and the prefix/fuzzy
    fallback in :mod:`analysis.resolve`); unresolved → ``unresolved``.
    """
    out: list[NameResolution] = []
    for _row, typed, fid, _sugg in resolved_rows:
        if fid is None:
            out.append(NameResolution(
                typed=typed, form_id=None,
                resolved_display_name=None, via="unresolved",
            ))
            continue
        row = repo.get_form(conn, fid)
        display = row["display_name"] if row else None
        via = (
            "exact"
            if display and typed.strip().lower() == display.lower()
            else "alias"
        )
        out.append(NameResolution(
            typed=typed, form_id=fid,
            resolved_display_name=display, via=via,
        ))
    return tuple(out)


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
