"""Damage-calculator port of CotC's V1.1 spreadsheet plus the full bucket model.

Two layers, mirrored against the docs in ``buff_debuff/``:

- :mod:`damage.spreadsheet_calc` — 1:1 port of the V1.1 calculator at
  ``buff_debuff/COTC Effective Damage Calculator V1.1.xlsx``. Pure
  functions over the :class:`V11Inputs` dataclass.

- :mod:`damage.full_calc` — the canonical 6-group bucket model + final
  multipliers from ``buff_debuff/README.md``. Reduces to the
  spreadsheet calc when no extras are used (the parity test in
  ``tests/test_damage.py`` proves this on Public1's inputs).
"""

from .spreadsheet_calc import (
    damage_difference,
    effective_value,
    modified_base_stat,
)
from .types import (
    CORE_STATS,
    DEFAULT_SUB_BUCKET_CAP,
    ELEMENTAL,
    ELEMENTS,
    JP_OVERRIDE_CAP,
    PHYSICAL,
    V11Inputs,
    WEAPONS,
)
from . import full_calc

__all__ = [
    "CORE_STATS",
    "DEFAULT_SUB_BUCKET_CAP",
    "ELEMENTAL",
    "ELEMENTS",
    "JP_OVERRIDE_CAP",
    "PHYSICAL",
    "V11Inputs",
    "WEAPONS",
    "damage_difference",
    "effective_value",
    "full_calc",
    "modified_base_stat",
]
