"""Utility helpers for the ComAp integration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pycomap.configuration import SetpointDescription, ValueDescription

GROUP_UNGROUPED = "(ungrouped)"

# Pre-substitutions applied before generic slugification.
# Handles symbols that would otherwise collapse to empty or ambiguous tokens.
_PRE_SLUG = {
    "D+": "d_plus",
    "<>": "delta",
}

# Internal controller placeholder names — no entity should be created for these.
_PLACEHOLDER_NAMES = frozenset({"ST", "Not Used", ""})


def slugify(name: str) -> str:
    """Convert a controller value/setpoint name to a slug suitable for use as a translation key.

    Examples::

        slugify("Generator Voltage L1-N") == "generator_voltage_l1_n"
        slugify("D+")                     == "d_plus"
        slugify("Generator <> Frequency") == "generator_delta_frequency"
    """
    for src, dst in _PRE_SLUG.items():
        name = name.replace(src, dst)
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug


def entity_key(name: str) -> str | None:
    """Return the translation key slug for a controller name, or None to skip entity creation."""
    if name in _PLACEHOLDER_NAMES:
        return None
    return slugify(name)


def collect_groups(
    descs: list[ValueDescription] | list[SetpointDescription],
) -> list[str]:
    """Return a sorted list of unique group names for a set of descriptions.

    Descriptions with no group are placed in GROUP_UNGROUPED.
    Placeholder-named entries (entity_key returns None) are excluded.
    """
    return sorted(
        {
            (d.group if d.group is not None else GROUP_UNGROUPED)
            for d in descs
            if entity_key(d.name) is not None
        }
    )
