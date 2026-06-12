"""Heuristic gap detector.

Walks a DrawingJSON, finds fields that are null / empty / low-confidence,
and emits a list of GapItem with suggestions. No ML, no API calls — pure
Python so it's deterministic and debuggable.

Each rule is independent and can be toggled in RULES.
"""
from __future__ import annotations

from typing import Callable

from .schemas import DrawingJSON, GapItem, GapReport

# Each rule receives (drawing, ctx) and yields GapItem.
RuleFn = Callable[[DrawingJSON, dict], list[GapItem]]


def _ctx(image_width: int, image_height: int) -> dict:
    # Heuristic row bands for common placements; the cropper uses these to
    # build a default bbox when the model didn't return coordinates.
    return {
        "W": image_width,
        "H": image_height,
        "title_block_xyxy": (int(image_width * 0.55), int(image_height * 0.85),
                             image_width, image_height),
        "stamps_top": (int(image_width * 0.05), 0,
                       int(image_width * 0.95), int(image_height * 0.25)),
    }


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def rule_axes_spans_complete(d: DrawingJSON, ctx: dict) -> list[GapItem]:
    """If horizontal axes 1..N exist but spans_mm has gaps, flag the missing keys."""
    gaps: list[GapItem] = []
    axes = d.axes.horizontal or []
    spans = d.axes.spans_mm or {}
    if len(axes) < 2:
        return gaps
    for i in range(len(axes) - 1):
        key = f"{axes[i]}-{axes[i+1]}"
        if key not in spans:
            gaps.append(GapItem(
                field=f"axes.spans_mm.{key}",
                reason="missing_derivation",
                severity="high",
                bbox_xyxy=None,
                suggestion=f"Crop the dimension chain between axes {axes[i]} and {axes[i+1]}",
            ))
    return gaps


def rule_parapet_without_roof(d: DrawingJSON, ctx: dict) -> list[GapItem]:
    """parapet_top is set but no roof_top in 'other' → we can't compute height."""
    out: list[GapItem] = []
    if d.elevations.parapet_top and d.computed.parapet_height_mm is None:
        # Look for any other elevation that could plausibly be the roof.
        other = d.elevations.other or []
        roof_candidates = [e for e in other if any(
            k in e.lower() for k in ("кровл", "roof", "мембран", "гидроизол")
        )]
        if not roof_candidates:
            out.append(GapItem(
                field="computed.parapet_height_mm",
                reason="missing_derivation",
                severity="medium",
                bbox_xyxy=None,
                suggestion="No roof_top elevation found — re-ask the elevations column near the roofline",
            ))
    return out


def rule_assembly_thickness_null(d: DrawingJSON, ctx: dict) -> list[GapItem]:
    out: list[GapItem] = []
    for i, asm in enumerate(d.assemblies):
        for j, layer in enumerate(asm.layers_top_to_bottom):
            if layer.thickness_mm is None and layer.material:
                # The thickness is usually on a leader line next to the layer.
                # We don't know the exact bbox, so suggest a top-down crop.
                out.append(GapItem(
                    field=f"assemblies[{i}].layers_top_to_bottom[{j}].thickness_mm",
                    reason="null",
                    severity="medium",
                    bbox_xyxy=None,
                    suggestion=f"Crop the row for material '{layer.material[:40]}'",
                ))
    return out


def rule_node_marks_without_assembly(d: DrawingJSON, ctx: dict) -> list[GapItem]:
    out: list[GapItem] = []
    asm_ids = {a.id for a in d.assemblies}
    for m in d.node_marks:
        if m not in asm_ids and m not in (d.node_marks_referenced or {}):
            out.append(GapItem(
                field=f"node_marks[{m}]",
                reason="missing_derivation",
                severity="low",
                bbox_xyxy=None,
                suggestion=f"Find node '{m}' on the sheet and crop its detail",
            ))
    return out


def rule_title_block_missing(d: DrawingJSON, ctx: dict) -> list[GapItem]:
    out: list[GapItem] = []
    tb = d.title_block
    if not tb.project_code:
        out.append(GapItem(
            field="title_block.project_code",
            reason="null",
            severity="medium",
            bbox_xyxy=list(ctx["title_block_xyxy"]),
            suggestion="Crop the title block in the bottom-right",
        ))
    return out


def rule_quantities_empty(d: DrawingJSON, ctx: dict) -> list[GapItem]:
    if not d.quantities and (d.assemblies or d.axes.spans_mm):
        return [GapItem(
            field="quantities",
            reason="missing_derivation",
            severity="low",
            bbox_xyxy=None,
            suggestion="No explicit quantities on sheet — derive from axes + assemblies",
        )]
    return []


RULES: list[RuleFn] = [
    rule_axes_spans_complete,
    rule_parapet_without_roof,
    rule_assembly_thickness_null,
    rule_node_marks_without_assembly,
    rule_title_block_missing,
    rule_quantities_empty,
]


# ---------------------------------------------------------------------------
# Score: fraction of "interesting" fields that are populated.
# ---------------------------------------------------------------------------

INTERESTING = [
    ("drawing_id", lambda d: d.drawing_id),
    ("axes.horizontal", lambda d: d.axes.horizontal),
    ("axes.spans_mm", lambda d: d.axes.spans_mm),
    ("elevations.parapet_top", lambda d: d.elevations.parapet_top),
    ("elevations.ground", lambda d: d.elevations.ground),
    ("assemblies", lambda d: d.assemblies),
    ("node_marks", lambda d: d.node_marks),
    ("title_block.project_code", lambda d: d.title_block.project_code),
    ("computed.parapet_height_mm", lambda d: d.computed.parapet_height_mm),
]


def _populated(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return False
    return True


def score(drawing: DrawingJSON) -> float:
    hits = sum(1 for _, fn in INTERESTING if _populated(fn(drawing)))
    return round(hits / len(INTERESTING), 3)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def detect_gaps(drawing: DrawingJSON, image_path: str,
                image_width: int, image_height: int) -> GapReport:
    ctx = _ctx(image_width, image_height)
    gaps: list[GapItem] = []
    for rule in RULES:
        try:
            gaps.extend(rule(drawing, ctx))
        except Exception as e:
            # A buggy rule should never break the pipeline.
            gaps.append(GapItem(
                field=f"<rule {rule.__name__}>",
                reason="null",
                severity="low",
                suggestion=f"rule error: {e}",
            ))
    return GapReport(
        drawing_id=drawing.drawing_id,
        image_path=image_path,
        image_width=image_width,
        image_height=image_height,
        gaps=gaps,
        total_score=score(drawing),
    )
