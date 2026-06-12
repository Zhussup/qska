"""Derive estimate lines from a DrawingJSON.

This is intentionally simple — it just maps recognised materials and
assemblies to hardcoded ГЭСН codes and computes quantities from
axes + computed values. The point is to unblock the React UI; production
estimate logic lives elsewhere.
"""
from __future__ import annotations

import re
from typing import Callable

from . import gesn
from .schemas import DrawingJSON, EstimateLine, EstimateResult


# ---------------------------------------------------------------------------
# Quantity producers
# ---------------------------------------------------------------------------

def _axis_area(d: DrawingJSON) -> float:
    """Rough building footprint in m² from axis spans."""
    bd = d.axes.building_dimensions_mm or {}
    length_mm = bd.get("length") or 0
    width_mm = bd.get("width") or 0
    if not (length_mm and width_mm):
        # Fall back to summing horizontal spans * vertical span.
        h_sum = sum((d.axes.spans_mm or {}).values()) or 0
        v_keys = [v for k, v in (d.axes.spans_mm or {}).items()
                  if d.axes.vertical and k.split("-")[0] in d.axes.vertical]
        v_span = v_keys[0] if v_keys else 0
        if h_sum and v_span:
            return (h_sum * v_span) / 1_000_000
        return 0.0
    return (length_mm * width_mm) / 1_000_000


def _parapet_length(d: DrawingJSON) -> float:
    """Parapet length in metres from horizontal spans."""
    return sum((d.axes.spans_mm or {}).values()) / 1000.0


def _insulation_thickness_mm(d: DrawingJSON) -> float:
    total = 0.0
    for asm in d.assemblies:
        for layer in asm.layers_top_to_bottom:
            mat = (layer.material or "").lower()
            if any(k in mat for k in ("пенополистирол", "утеплитель", "xps", "карбон", "псбс")):
                total += layer.thickness_mm or 0
    return total


# ---------------------------------------------------------------------------
# Material classifier
# ---------------------------------------------------------------------------

def _classify(material: str) -> str | None:
    """Return a GESN key if the material matches an MVP rule, else None."""
    m = material.lower()
    rules: list[tuple[str, str]] = [
        ("tpo_membrane",                 "тпо"),
        ("roof_waterproofing_membrane",  "мембран"),
        ("geotextile",                   "геотекстил"),
        ("insulation_xps",               ("карбон", "xps", "экструдир")),
        ("insulation_psb",               ("пенополистирол м45", "псбс")),
        ("wall_brick_380",               ("кирпич", "380")),
        ("wall_brick_250",               ("кирпич", "250")),
        ("prof_list_n75",                "н75"),
        ("metal_beam",                   "металлическ"),
        ("hpl_panel",                    "hpl"),
        ("hpl_panel",                    "fundermax"),
        ("sfb_panel",                    ("стеклофибробетон", "сфб")),
        ("concrete_prep",                ("бетонн", "подготовк")),
        ("concrete_slab_200",            ("ж/б плита", "монолитн")),
        ("screed",                       ("стяжк", "пескобетон")),
        ("glazing_vitrage",              ("витраж", "алюминиев", "стеклопакет")),
    ]
    for key, needle in rules:
        if isinstance(needle, tuple):
            if all(n in m for n in needle):
                return key
        else:
            if needle in m:
                return key
    return None


# ---------------------------------------------------------------------------
# Rules → estimate lines
# ---------------------------------------------------------------------------

# Each rule produces 0..N estimate lines. Order matters — earlier rules
# can suppress later ones by tagging used material names.
RULES: list[Callable[[DrawingJSON, set[str]], list[EstimateLine]]] = []


def _used_materials(d: DrawingJSON) -> set[str]:
    return {layer.material for asm in d.assemblies for layer in asm.layers_top_to_bottom
            if layer.material}


def rule_membranes(d: DrawingJSON, used: set[str]) -> list[EstimateLine]:
    lines: list[EstimateLine] = []
    for asm in d.assemblies:
        if (asm.type or "").lower() not in ("кровля", "krovlya", "roof"):
            continue
        for layer in asm.layers_top_to_bottom:
            key = _classify(layer.material)
            if not key:
                continue
            entry = gesn.GESN_MVP[key]
            area = _axis_area(d)
            if not area:
                continue
            if layer.material in used:
                used.discard(layer.material)
            lines.append(EstimateLine(
                gesn_code=entry["code"],
                description=f"{entry['description']} — '{layer.material[:60]}'",
                unit=entry["unit"],
                quantity=round(area, 2),
                unit_price=gesn.unit_price(entry["code"]),
                total=round(area * (gesn.unit_price(entry["code"]) or 0), 2),
                source_fields=[f"assemblies[id={asm.id}].layers[{layer.material}]"],
            ))
    return lines


def rule_insulation(d: DrawingJSON, used: set[str]) -> list[EstimateLine]:
    lines: list[EstimateLine] = []
    area = _axis_area(d)
    if not area:
        return lines
    for asm in d.assemblies:
        if (asm.type or "").lower() not in ("кровля", "krovlya", "roof", "пол", "floor"):
            continue
        for layer in asm.layers_top_to_bottom:
            key = _classify(layer.material)
            if key not in ("insulation_xps", "insulation_psb"):
                continue
            entry = gesn.GESN_MVP[key]
            lines.append(EstimateLine(
                gesn_code=entry["code"],
                description=f"{entry['description']} — '{layer.material[:60]}'",
                unit=entry["unit"],
                quantity=round(area, 2),
                unit_price=gesn.unit_price(entry["code"]),
                total=round(area * (gesn.unit_price(entry["code"]) or 0), 2),
                source_fields=[f"assemblies[id={asm.id}].layers[{layer.material}]"],
            ))
    return lines


def rule_walls_and_cladding(d: DrawingJSON, used: set[str]) -> list[EstimateLine]:
    lines: list[EstimateLine] = []
    area = _axis_area(d)
    for asm in d.assemblies:
        if (asm.type or "").lower() not in ("фасад", "fasad", "стена", "wall"):
            continue
        for layer in asm.layers_top_to_bottom:
            key = _classify(layer.material)
            if key not in ("sfb_panel", "hpl_panel"):
                continue
            entry = gesn.GESN_MVP[key]
            if not area:
                continue
            lines.append(EstimateLine(
                gesn_code=entry["code"],
                description=f"{entry['description']} — '{layer.material[:60]}'",
                unit=entry["unit"],
                quantity=round(area, 2),
                unit_price=gesn.unit_price(entry["code"]),
                total=round(area * (gesn.unit_price(entry["code"]) or 0), 2),
                source_fields=[f"assemblies[id={asm.id}].layers[{layer.material}]"],
            ))
    return lines


def rule_glazing_from_quantities(d: DrawingJSON, used: set[str]) -> list[EstimateLine]:
    """If the model reported an explicit glazing area, trust it."""
    lines: list[EstimateLine] = []
    for q in d.quantities:
        if "остеклен" in q.name.lower() or "витраж" in q.name.lower():
            entry = gesn.GESN_MVP["glazing_vitrage"]
            lines.append(EstimateLine(
                gesn_code=entry["code"],
                description=entry["description"],
                unit=entry["unit"],
                quantity=q.value,
                unit_price=gesn.unit_price(entry["code"]),
                total=round(q.value * (gesn.unit_price(entry["code"]) or 0), 2),
                source_fields=[f"quantities[{q.name}]"],
            ))
    return lines


def rule_parapet(d: DrawingJSON, used: set[str]) -> list[EstimateLine]:
    if d.computed.parapet_height_mm is None or d.computed.parapet_length_mm is None:
        return []
    entry = gesn.GESN_MVP["parapet_brick"]
    length_m = d.computed.parapet_length_mm / 1000
    return [EstimateLine(
        gesn_code=entry["code"],
        description=f"{entry['description']} h={d.computed.parapet_height_mm} мм",
        unit=entry["unit"],
        quantity=round(length_m, 2),
        unit_price=gesn.unit_price(entry["code"]),
        total=round(length_m * (gesn.unit_price(entry["code"]) or 0), 2),
        source_fields=["computed.parapet_height_mm", "computed.parapet_length_mm"],
    )]


RULES.extend([rule_membranes, rule_insulation, rule_walls_and_cladding,
              rule_glazing_from_quantities, rule_parapet])


def estimate(drawing: DrawingJSON) -> EstimateResult:
    used = _used_materials(drawing)
    lines: list[EstimateLine] = []
    for rule in RULES:
        try:
            lines.extend(rule(drawing, used))
        except Exception as e:
            # A buggy rule should not break the result; surface as a placeholder.
            lines.append(EstimateLine(
                gesn_code="ERR", description=f"rule {rule.__name__} failed: {e}",
                unit="-", quantity=0.0,
            ))
    # Dedup by (code, source_fields) — keep the first.
    seen: set[tuple] = set()
    deduped: list[EstimateLine] = []
    for ln in lines:
        sig = (ln.gesn_code, ln.source_fields[0] if ln.source_fields else "")
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(ln)
    total = sum(ln.total or 0.0 for ln in deduped)
    return EstimateResult(
        drawing_id=drawing.drawing_id,
        lines=deduped,
        total=round(total, 2),
        currency="RUB",
    )


def export_excel(result: EstimateResult, path: str) -> None:
    """Write a single-sheet .xlsx that an estimator can paste into a real смета."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = (result.drawing_id or "estimate")[:30]
    headers = ["#", "ГЭСН/ФЕР", "Описание работ", "Ед.", "Кол-во", "Цена ед.", "Сумма"]
    ws.append(headers)
    for i, ln in enumerate(result.lines, 1):
        ws.append([
            i,
            ln.gesn_code,
            ln.description,
            ln.unit,
            ln.quantity,
            ln.unit_price or 0,
            ln.total or 0,
        ])
    ws.append([])
    ws.append(["", "", "", "", "", "ИТОГО:", result.total])
    wb.save(path)
