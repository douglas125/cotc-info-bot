"""Team-analysis package — pure logic for /analyze_team.

No `discord` import in this package; everything here is callable from the
audit CLI (`python -m analysis.audit`) and from `bot/team_commands.py`
without requiring the bot runtime to be installed.

Phase 1 scaffolding: the modules wire up end to end but the pattern table
in :mod:`analysis.patterns` is empty, so every skill classifies as
``unparsed``. Phase 2 populates patterns from user-supplied team
examples; see ``buff_debuff/`` for the math reference and
``buff_debuff/damage_cap_and_potency.md`` for the cap-up model.
"""
