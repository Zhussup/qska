"""Materials pass (the COLOUR pass).

Unlike pass1/pass2 — which run on a grayscale image because line-work loses
nothing in gray — this pass MUST keep colour: architectural sheets encode
material layers as colour-coded hatching with a legend, and the legend colour
is the only thing that maps a hatch to its material. So we send the full
colour image (encode_image(..., grayscale=False)).

Two jobs in one Gemini call:
  1. Read every specification / экспликация / ведомость table on the sheet
     into structured rows (name, поз., unit, qty).
  2. Read the colour legend and map each legend colour → material, returning
     the colour as #RRGGBB so the UI can show a swatch.

Then `reconcile()` (pure Python, no API) compares the assembly layers that
pass2 produced against this material list and flags mismatches both ways.
"""
from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from . import config
from .extractor import call_gemini, extract_json
from .schemas import (DrawingJSON, MaterialItem, MaterialsResult,
                      ReconcileItem, ReconcileReport)

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

PROMPT_MATERIALS = """You are reading ONE architectural/construction drawing IN COLOUR.
Your task is to build a single list of materials from TWO sources on the sheet.

SOURCE 1 — TABLES. Find every specification / ведомость / экспликация /
"спецификация материалов" table. For each row extract:
  - name      : material name exactly as written
  - position  : the "Поз." / item number, or null
  - unit      : unit of measure (м2, м3, м.п., шт, кг, т), or null
  - quantity  : numeric quantity if the row has one, else null

SOURCE 2 — COLOUR LEGEND / HATCHING. Many sheets colour-code the layers of a
roof/wall/floor build-up and give a colour legend ("условные обозначения").
For each legend entry extract:
  - name      : the material the colour stands for
  - color_hex : the legend swatch colour as #RRGGBB (your best estimate)
  - unit/quantity/position: usually null for legend-only entries

MERGE: if the same material appears in BOTH a table and the legend, output ONE
entry with source="both" and fill color_hex from the legend.

For every entry set:
  - source: "table" | "color" | "both"

RULES:
- Do NOT invent materials. Only what is printed on the sheet.
- Keep names in the original language (Russian/Kazakh) as written.
- If there is no table and no legend, return an empty materials list.
- Return ONLY valid JSON, no markdown fences, no commentary.

Schema:
{
  "tables_found": <int>,
  "legend_found": <true|false>,
  "materials": [
    {"name": "str", "position": "str|null", "unit": "str|null",
     "quantity": <number|null>, "color_hex": "#RRGGBB|null",
     "source": "table|color|both", "note": "str|null"}
  ]
}"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_materials(image_path: Path, model: str | None = None) -> MaterialsResult:
    """Run the colour pass. Sends the FULL-COLOUR image (grayscale=False)."""
    model = model or config.EXTRACTION_MODEL
    text, p, o, dt = call_gemini(model, image_path, PROMPT_MATERIALS, grayscale=False)
    parsed, _ = extract_json(text)

    materials: list[MaterialItem] = []
    tables_found = 0
    legend_found = False
    if parsed:
        tables_found = int(parsed.get("tables_found") or 0)
        legend_found = bool(parsed.get("legend_found") or False)
        for row in parsed.get("materials", []) or []:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            try:
                materials.append(MaterialItem(
                    name=name,
                    position=_str_or_none(row.get("position")),
                    unit=_str_or_none(row.get("unit")),
                    quantity=_num_or_none(row.get("quantity")),
                    color_hex=_hex_or_none(row.get("color_hex")),
                    source=row.get("source") if row.get("source") in ("table", "color", "both") else "table",
                    note=_str_or_none(row.get("note")),
                ))
            except Exception:
                continue

    return MaterialsResult(
        drawing_id=image_path.stem,
        materials=materials,
        tables_found=tables_found,
        legend_found=legend_found,
        raw=text,
        latency_s=round(dt, 2),
        tokens_in=p,
        tokens_out=o,
    )


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num_or_none(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _hex_or_none(v) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    m = re.match(r"^#?([0-9a-fA-F]{6})$", s)
    return f"#{m.group(1).lower()}" if m else None


# ---------------------------------------------------------------------------
# Reconciliation: assembly layers (pass2) vs the spec material list
# ---------------------------------------------------------------------------

_NORMALISE_RE = re.compile(r"[^\wа-яё]+", re.IGNORECASE)


def _normalise(name: str) -> str:
    """Lowercase, strip punctuation/units noise for fuzzy comparison."""
    s = name.lower().strip()
    s = _NORMALISE_RE.sub(" ", s)
    return " ".join(s.split())


def _similar(a: str, b: str) -> float:
    """Robust name similarity: max of char-sequence ratio and token Jaccard.

    Spec names and assembly-layer names often share the same words in a
    different order ("ТПО мембрана" vs "Мембрана ТПО Logicroof"), which a pure
    SequenceMatcher under-scores. Token overlap recovers those.
    """
    na, nb = _normalise(a), _normalise(b)
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
    else:
        jaccard = 0.0
    return max(seq, jaccard)


_MATCH_THRESHOLD = 0.6


def reconcile(drawing: DrawingJSON, materials: MaterialsResult) -> ReconcileReport:
    """Cross-check assembly-layer materials against the specification list.

    Pure Python: fuzzy-matches each assembly material name against each spec
    name. Anything matched is "matched"; the rest split into only_in_assembly
    (in the build-up but missing from the spec table) and only_in_spec (listed
    but never used in an assembly).
    """
    # Gather assembly materials with the assembly ids that reference them.
    asm_materials: dict[str, list[str]] = {}
    for asm in drawing.assemblies:
        for layer in asm.layers_top_to_bottom:
            if layer.material:
                asm_materials.setdefault(layer.material, []).append(asm.id)

    spec_unmatched = list(materials.materials)
    items: list[ReconcileItem] = []
    matched = only_asm = only_spec = 0

    for asm_name, refs in asm_materials.items():
        best = None
        best_score = 0.0
        for spec in spec_unmatched:
            sc = _similar(asm_name, spec.name)
            if sc > best_score:
                best_score, best = sc, spec
        if best is not None and best_score >= _MATCH_THRESHOLD:
            spec_unmatched.remove(best)
            matched += 1
            items.append(ReconcileItem(
                name=asm_name, status="matched", in_assembly=True, in_spec=True,
                matched_to=best.name, spec_unit=best.unit,
                spec_quantity=best.quantity, assembly_refs=sorted(set(refs)),
            ))
        else:
            only_asm += 1
            items.append(ReconcileItem(
                name=asm_name, status="only_in_assembly", in_assembly=True,
                in_spec=False, assembly_refs=sorted(set(refs)),
            ))

    # Whatever spec material is left over was never used in an assembly.
    for spec in spec_unmatched:
        only_spec += 1
        items.append(ReconcileItem(
            name=spec.name, status="only_in_spec", in_assembly=False,
            in_spec=True, spec_unit=spec.unit, spec_quantity=spec.quantity,
        ))

    # Stable ordering: problems first (only_in_*), matched last.
    order = {"only_in_assembly": 0, "only_in_spec": 1, "matched": 2}
    items.sort(key=lambda it: (order.get(it.status, 3), it.name.lower()))

    return ReconcileReport(
        drawing_id=drawing.drawing_id,
        items=items,
        matched=matched,
        only_in_assembly=only_asm,
        only_in_spec=only_spec,
    )
