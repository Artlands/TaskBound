"""Placement sampling and injection application (plan §6.5, §11 phase 2).

An injection declares a `placement_class` — a set of admissible positions for
its entry point — not a byte offset. The runner samples one position per run
from a seeded RNG and records it, so placement enters the analysis as a random
effect rather than an unexamined constant.

A placement class that resolves to nothing is a hard failure, never a silently
clean run.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Any

from .policy import resolve


class PlacementError(Exception):
    """The placement class resolved to no admissible position."""


@dataclass
class Placement:
    placement_class: str
    file: str
    line: int

    @property
    def placement_id(self) -> str:
        return f"{self.placement_class}@{self.line}"


def sample_placement(
    workspace_root: str,
    placement_classes: dict[str, Any],
    placement_class: str,
    seed: int,
) -> Placement:
    spec = placement_classes.get(placement_class)
    if spec is None:
        raise PlacementError(f"unknown placement class {placement_class!r}")
    target = spec["file"]
    real = resolve(workspace_root, target).real
    if not os.path.isfile(real):
        raise PlacementError(f"placement class {placement_class!r} targets missing file {target!r}")
    with open(real, encoding="utf-8") as fh:
        line_count = len(fh.read().splitlines())
    positions = [p for p in spec["line_positions"] if 0 <= p <= line_count]
    if not positions:
        raise PlacementError(
            f"placement class {placement_class!r} has no admissible position in {target!r}"
        )
    rng = random.Random(seed)
    return Placement(placement_class=placement_class, file=target, line=rng.choice(positions))


def apply_injection(workspace_root: str, placement: Placement, text: str) -> str:
    """Insert `text` at the sampled position. Returns the exact inserted block."""
    real = resolve(workspace_root, placement.file).real
    with open(real, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    block = text.rstrip("\n").split("\n")
    lines[placement.line : placement.line] = block
    with open(real, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return "\n".join(block)


def exposure_probe(block: str) -> str:
    """The substring whose appearance in a tool result proves exposure.

    The longest non-blank line of the injected block: distinctive enough not to
    match host content, short enough to survive a tool result that truncates.
    """
    candidates = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not candidates:
        raise PlacementError("injection text is empty")
    return max(candidates, key=len)
