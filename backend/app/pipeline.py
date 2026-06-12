"""Full-cycle pipeline: extract → gaps → auto-fill via crop_reask → estimate.

Designed for the "I am not a smetchik, just press the button" UX. One
function call, one artifact folder, status returned to the UI.

Caching: every step is keyed by the SHA-256 of the image bytes. If a step
already produced an artefact for this hash (and `--force` is False), we
read it from disk instead of re-calling Gemini. This is the difference
between burning the free-tier quota in 20 clicks vs 2000.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import config, crop_reask as crop_mod, estimate_engine, extractor, gap_detector
from .schemas import DrawingJSON, Pass1Facts


def image_hash(image_path: Path) -> str:
    """Stable hash of an image's contents. If the file changes, hash changes,
    cache is invalidated automatically. Cheap (no decode)."""
    h = hashlib.sha256()
    h.update(image_path.read_bytes())
    return h.hexdigest()[:16]


def cache_key(drawing_id: str, h: str) -> str:
    """Composite key: drawing_id + content hash. Survives renames but
    invalidates on edits."""
    return f"{drawing_id}__{h}"


@dataclass
class CycleStep:
    name: str
    ok: bool
    duration_s: float = 0.0
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class CycleResult:
    drawing_id: str
    image_path: str
    steps: list[CycleStep] = field(default_factory=list)
    pass1: Pass1Facts | None = None
    pass2: DrawingJSON | None = None
    pass3: dict | None = None
    auto_detect: list[dict] = field(default_factory=list)
    gap_report: Any = None
    final_gaps: Any = None
    estimate: Any = None
    total_latency_s: float = 0.0
    total_tokens: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _step(name: str, fn) -> tuple[CycleStep, Any]:
    """Time and execute one step. Step never raises; failures are recorded."""
    t0 = time.time()
    step = CycleStep(name=name, ok=True)
    try:
        result = fn()
    except Exception as e:
        step.ok = False
        step.note = f"{type(e).__name__}: {e}"
        step.duration_s = round(time.time() - t0, 2)
        return step, None
    step.duration_s = round(time.time() - t0, 2)
    return step, result


def _cached(name: str, source: str, payload: Any, latency: float) -> CycleStep:
    """Build a CycleStep that represents a cache hit (no API call)."""
    return CycleStep(
        name=name, ok=True, duration_s=round(latency, 3),
        note=f"cache hit ({source})", detail={},
    )


# ---------------------------------------------------------------------------
# Pass3 = merge(Pass1, Pass2)
# ---------------------------------------------------------------------------
# Heuristics that decide which Pass1 fact maps to which Pass2 field.
# Both sides are matched by either the `points_to` label (Pass1) or a
# substring of it (Pass2). Order matters: more specific first.

PASS3_MERGE_RULES: list[tuple[str, str]] = [
    # (regex in pass1.points_to / value,  pass2 dot-path)
    (r"top\s*of\s*the\s*parapet",         "elevations.parapet_top"),
    (r"parapet\s*wall|top\s*of\s*parapet", "elevations.parapet_top"),
    (r"top\s*of\s*the\s*ridge|ridge",     "elevations.ridge"),
    (r"top\s*of\s*the\s*roof|roof\s*membrane|top\s*of\s*roof",
                                        "elevations.roof_top_inferred"),
    (r"ground\s*level|ground",            "elevations.ground"),
    (r"top\s*of\s*the\s*ground\s*slab|terrace\s*level", "elevations.ground"),
    (r"top\s*of\s*the\s*canopy\s*slab|top\s*of\s*the\s*canopy",
                                        "elevations.canopy_top_inferred"),
    (r"top\s*of\s*the\s*window\s*frame|window\s*frame|window\s*opening",
                                        "elevations.window_top_inferred"),
    (r"top\s*of\s*the\s*tall\s*dark\s*volume|top\s*of\s*tall",
                                        "elevations.upper_volume_inferred"),
    (r"building\s*height|top\s*of\s*ridge\s*overall", "elevations.ridge"),
]


def _match_pass1_to_pass2(points_to: str) -> str | None:
    """Return the pass2 dot-path that this pass1 fact most likely maps to."""
    s = (points_to or "").lower()
    for pattern, path in PASS3_MERGE_RULES:
        import re as _re
        if _re.search(pattern, s):
            return path
    return None


def _set_by_path(obj: dict, path: str, value: Any) -> bool:
    """Set value at dot.path into nested dicts. Returns True on success."""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        if not isinstance(cur, dict):
            return False
        cur = cur.setdefault(p, {})
    return isinstance(cur, dict) and parts[-1] in cur or (
        isinstance(cur, dict) and (parts[-1] in cur or True)
    ) and (cur.__setitem__(parts[-1], value) or True)


def _is_duplicate(current: Any, new: Any) -> bool:
    """Decide if a new value matches the existing one or is clearly the same."""
    if current is None:
        return False
    if isinstance(current, str) and isinstance(new, str):
        return current.strip().lstrip("+").rstrip("0").rstrip(".") == \
               new.strip().lstrip("+").rstrip("0").rstrip(".")
    return current == new


def merge_pass1_into_pass2(facts: Pass1Facts, drawing: DrawingJSON) -> dict[str, Any]:
    """Build Pass3 (the unified view) by merging pass1 facts into pass2 fields.

    The pass2 instance is *not* mutated. We return a serialisable dict with:
      - pass3: the merged DrawingJSON (deep-copied)
      - applied: list[{path, value, source_pass1_idx}] — fields that pass1 filled
      - conflicts: list[{path, pass1_value, pass2_value}] — both sets disagree
      - new_nulls: list of pass2 fields still null after merge
    """
    import copy
    raw = copy.deepcopy(drawing.model_dump())

    applied: list[dict] = []
    conflicts: list[dict] = []

    for i, e in enumerate(facts.elevations):
        path = _match_pass1_to_pass2(e.points_to)
        if not path:
            continue
        # Read current value
        cur = raw
        for p in path.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(p) if p in cur else cur.get(p, None)
        if _is_duplicate(cur, e.value):
            continue
        if cur is not None and not _is_duplicate(cur, e.value):
            # Both pass2 and pass1 have a value but they disagree.
            conflicts.append({"path": path, "pass1_value": e.value,
                               "pass2_value": cur,
                               "pass1_bbox": e.bbox_xyxy})
        else:
            # Walk the path and set
            cur = raw
            parts = path.split(".")
            for p in parts[:-1]:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.setdefault(p, {})
            if isinstance(cur, dict):
                cur[parts[-1]] = e.value
                applied.append({"path": path, "value": e.value,
                                 "source_pass1_idx": i,
                                 "bbox_xyxy": e.bbox_xyxy})

    new_drawing = DrawingJSON.model_validate(raw)

    # Find fields that are still null (Pass3 still incomplete)
    new_nulls: list[str] = []
    for rule in gap_detector.RULES:
        for g in rule(new_drawing, {}):
            new_nulls.append(g.field)

    return {
        "pass3": new_drawing.model_dump(),
        "applied": applied,
        "conflicts": conflicts,
        "new_nulls": list(dict.fromkeys(new_nulls)),  # dedup, preserve order
    }


def predict_bbox_for_nulls(image_path: Path, drawing: DrawingJSON,
                           facts: Pass1Facts) -> list[dict]:
    """For each null field, predict an approximate bbox where the value
    might be on the sheet, based on Pass1's bbox distribution.

    Strategy:
      1. If pass1 contains a fact whose `points_to` maps to the null field
         AND it has a bbox — use that bbox.
      2. Otherwise, return null bbox; UI falls back to manual / scan.
    """
    out: list[dict] = []
    # Re-detect gaps on the merged drawing
    w, h = extractor.image_size(image_path)
    report = gap_detector.detect_gaps(drawing, str(image_path), w, h)

    # Build a quick lookup of pass1 fact -> bbox
    fact_bbox: dict[str, list[int] | None] = {}
    for e in facts.elevations:
        p = _match_pass1_to_pass2(e.points_to)
        if p and e.bbox_xyxy:
            fact_bbox[p] = e.bbox_xyxy

    for g in report.gaps:
        # Only high/medium priority get auto-prediction
        if g.severity == "low":
            continue
        # Try to find a matching pass1 bbox
        pred = fact_bbox.get(g.field)
        out.append({
            "field": g.field,
            "severity": g.severity,
            "predicted_bbox_xyxy": pred,
            "method": "pass1_match" if pred else "manual_required",
        })
    return out


# ---------------------------------------------------------------------------
# Inject merge + predict_bbox as pipeline steps inside run_full_cycle
# ---------------------------------------------------------------------------

def _merge_step(result, facts, drawing, force, art_gaps, key):
    """Build Pass3 from Pass1+Pass2; persist as a separate artefact."""
    t0 = time.time()
    cache = art_gaps / f"{key}__pass3.json"
    if not force and cache.exists():
        import json as _json
        return _cached("merge_pass1_pass2_to_pass3", "json",
                       _json.loads(cache.read_text(encoding="utf-8")),
                       time.time() - t0)
    merged = merge_pass1_into_pass2(facts, drawing)
    cache.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    step = CycleStep(
        name="merge_pass1_pass2_to_pass3",
        ok=True,
        duration_s=round(time.time() - t0, 4),
        note=f"applied={len(merged['applied'])} conflicts={len(merged['conflicts'])}",
        detail={
            "applied_count": len(merged["applied"]),
            "conflict_count": len(merged["conflicts"]),
            "remaining_nulls": len(merged["new_nulls"]),
        },
    )
    return step, merged


def _auto_detect_step(result, image_path, drawing, facts, force, art_gaps, key):
    t0 = time.time()
    cache = art_gaps / f"{key}__auto_detect.json"
    if not force and cache.exists():
        import json as _json
        return _cached("auto_detect_bbox_for_nulls", "json",
                       _json.loads(cache.read_text(encoding="utf-8")),
                       time.time() - t0)
    predictions = predict_bbox_for_nulls(image_path, drawing, facts)
    cache.write_text(json.dumps(predictions, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    step = CycleStep(
        name="auto_detect_bbox_for_nulls",
        ok=True,
        duration_s=round(time.time() - t0, 4),
        note=f"{len(predictions)} predictions",
        detail={"predicted": len(predictions),
                "with_bbox": sum(1 for p in predictions if p["predicted_bbox_xyxy"])},
    )
    return step, predictions


def run_full_cycle(image_path: Path, drawing_id: str | None = None,
                   model: str | None = None,
                   crop_model: str | None = None,
                   max_auto_reask: int = 6,
                   force: bool = False) -> CycleResult:
    """One-button pipeline with content-hash caching.

    Pass 1 and Pass 2 are cached on disk under
    `artifacts/json/{cache_key}__pass1.json` etc., keyed by
    `sha256(image_bytes)[:16]`. If the file hasn't changed and `force` is
    False, we just re-load the JSON — zero Gemini calls, zero token cost.

    Pass-1-facts are not just a "raw response" — they are the JSON model
    returned. Pass 2 is also cached, so we can iterate on
    prompt-engineering and gap rules offline, then re-run estimate for free.

    The user-facing artefacts (e.g. `{drawing_id}__pass1.json`) are written
    as well so the React UI doesn't need to know about cache keys.
    """
    drawing_id = drawing_id or image_path.stem
    art_json = config.ARTIFACTS_DIR / "json"
    art_gaps = config.ARTIFACTS_DIR / "gaps"
    art_est = config.ARTIFACTS_DIR / "estimates"
    for d in (art_json, art_gaps, art_est):
        d.mkdir(parents=True, exist_ok=True)

    # Content-hash based cache key. If the user edits the PNG, hash changes,
    # cache invalidates automatically.
    t_hash = time.time()
    img_h = image_hash(image_path)
    key = cache_key(drawing_id, img_h)
    hash_dt = time.time() - t_hash

    p1_cache = art_json / f"{key}__pass1.json"
    p2_cache = art_json / f"{key}__pass2.json"
    gaps_cache = art_gaps / f"{key}__gaps.json"
    estimate_cache = art_est / f"{key}__estimate.json"

    result = CycleResult(drawing_id=drawing_id, image_path=str(image_path))
    t_total = time.time()
    total_in = 0
    total_out = 0
    cache_hits = 0

    # -- Step 1: pass1 (grounding) -------------------------------------------
    facts: Pass1Facts
    p1_meta: dict
    if not force and p1_cache.exists():
        t0 = time.time()
        facts = Pass1Facts.model_validate_json(p1_cache.read_text(encoding="utf-8"))
        p1_meta = {"model": "cache", "latency_s": 0.0, "prompt_tokens": 0, "output_tokens": 0}
        step = _cached("pass1_grounding", "json", facts, time.time() - t0)
        cache_hits += 1
    else:
        def _p1():
            f, m, text = extractor.run_pass1(image_path, model)
            p1_cache.write_text(f.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
            return (f, m, text)
        step, payload = _step("pass1_grounding", _p1)
        if not step.ok:
            result.error = step.note
            result.steps.append(step)
            return result
        facts, p1_meta, _ = payload

    result.pass1 = facts
    total_in += p1_meta.get("prompt_tokens") or 0
    total_out += p1_meta.get("output_tokens") or 0
    step.detail = {"latency_s": p1_meta["latency_s"],
                   "elevations": len(facts.elevations),
                   "dimensions": len(facts.dimensions),
                   "node_markers": len(facts.node_markers),
                   "bboxed": sum(1 for e in facts.elevations if e.bbox_xyxy)
                            + sum(1 for d in facts.dimensions if d.bbox_xyxy)
                            + sum(1 for n in facts.node_markers if n.bbox_xyxy),
                   "cache_key": key}
    result.steps.append(step)

    # Always write the user-facing alias so the React UI finds it.
    (art_json / f"{drawing_id}__pass1.json").write_text(
        facts.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")

    # -- Step 2: pass2 (semantic) -------------------------------------------
    drawing: DrawingJSON
    p2_meta: dict
    if not force and p2_cache.exists():
        t0 = time.time()
        drawing = DrawingJSON.model_validate_json(p2_cache.read_text(encoding="utf-8"))
        p2_meta = {"model": "cache", "latency_s": 0.0, "prompt_tokens": 0, "output_tokens": 0}
        step = _cached("pass2_semantic", "json", drawing, time.time() - t0)
        cache_hits += 1
    else:
        def _p2():
            d, m, text = extractor.run_pass2(image_path, facts, model)
            p2_cache.write_text(d.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
            return (d, m, text)
        step, payload = _step("pass2_semantic", _p2)
        if not step.ok:
            result.error = step.note
            result.steps.append(step)
            return result
        drawing, p2_meta, _ = payload

    result.pass2 = drawing
    total_in += p2_meta.get("prompt_tokens") or 0
    total_out += p2_meta.get("output_tokens") or 0
    step.detail = {"latency_s": p2_meta["latency_s"],
                   "assemblies": len(drawing.assemblies),
                   "spans": len(drawing.axes.spans_mm),
                   "parapet_top": drawing.elevations.parapet_top}
    result.steps.append(step)
    (art_json / f"{drawing_id}__pass2.json").write_text(
        drawing.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")

    # -- Step 2.5: build Pass3 (merge of pass1 + pass2) ----------------------
    step, merged = _merge_step(result, facts, drawing, force, art_gaps, key)
    result.steps.append(step)
    if merged and "pass3" in merged:
        drawing = DrawingJSON.model_validate(merged["pass3"])
        result.pass3 = merged  # includes applied / conflicts / new_nulls
        (art_json / f"{drawing_id}__pass3.json").write_text(
            drawing.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")

    # -- Step 2.7: auto-detect bboxes for remaining nulls --------------------
    step, predictions = _auto_detect_step(result, image_path, drawing, facts, force, art_gaps, key)
    result.steps.append(step)
    result.auto_detect = predictions
    (art_gaps / f"{drawing_id}__auto_detect.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- Step 3: gap detection -----------------------------------------------
    report = None
    if not force and gaps_cache.exists():
        t0 = time.time()
        from .schemas import GapReport
        report = GapReport.model_validate_json(gaps_cache.read_text(encoding="utf-8"))
        step = _cached("gap_detect", "json", report, time.time() - t0)
        cache_hits += 1
    else:
        def _gaps():
            w, h = extractor.image_size(image_path)
            r = gap_detector.detect_gaps(drawing, str(image_path), w, h)
            gaps_cache.write_text(r.model_dump_json(ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            return r
        step, report = _step("gap_detect", _gaps)
    if step.ok and report is not None:
        result.gap_report = report
        step.detail = {"score": report.total_score, "gaps": len(report.gaps),
                       "high": sum(1 for g in report.gaps if g.severity == "high"),
                       "medium": sum(1 for g in report.gaps if g.severity == "medium")}
    result.steps.append(step)
    (art_gaps / f"{drawing_id}__gaps.json").write_text(
        report.model_dump_json(ensure_ascii=False, indent=2) if report else "{}",
        encoding="utf-8")

    # -- Step 4: auto-fill via crop_reask -----------------------------------
    autofill_cache = art_gaps / f"{key}__autofill.json"
    if report and report.gaps:
        targets = [g for g in report.gaps if g.bbox_xyxy]
        targets.sort(key=lambda g: 0 if g.severity == "high" else 1 if g.severity == "medium" else 2)
        targets = targets[:max_auto_reask]

        if not force and autofill_cache.exists():
            t0 = time.time()
            applied = json.loads(autofill_cache.read_text(encoding="utf-8"))
            step = _cached("auto_fill_via_crop_reask", "json", applied, time.time() - t0)
            cache_hits += 1
        else:
            def _autofill():
                applied: list[dict[str, Any]] = []
                for g in targets:
                    try:
                        r = crop_mod.crop_reask(image_path, g.bbox_xyxy, g.field,
                                                g.suggestion, model=crop_model)
                        applied.append({"field": g.field, "value": r.value,
                                        "confidence": r.confidence})
                    except Exception as e:
                        applied.append({"field": g.field, "error": str(e)})
                autofill_cache.write_text(json.dumps(applied, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
                return applied
            step, applied = _step("auto_fill_via_crop_reask", _autofill)
        if step.ok:
            step.detail = {"attempted": len(targets),
                           "succeeded": sum(1 for a in applied if "value" in a),
                           "failed": sum(1 for a in applied if "error" in a)}
        result.steps.append(step)
        (art_gaps / f"{drawing_id}__autofill.json").write_text(
            json.dumps(applied if step.ok else [], ensure_ascii=False, indent=2),
            encoding="utf-8")
    else:
        result.steps.append(CycleStep(name="auto_fill_via_crop_reask", ok=True,
                                       note="no gaps with bbox", duration_s=0.0))
    result.final_gaps = report

    # -- Step 5: estimate ----------------------------------------------------
    if not force and estimate_cache.exists():
        t0 = time.time()
        from .schemas import EstimateResult
        est = EstimateResult.model_validate_json(estimate_cache.read_text(encoding="utf-8"))
        step = _cached("estimate", "json", est, time.time() - t0)
        cache_hits += 1
    else:
        def _estimate():
            est = estimate_engine.estimate(drawing)
            estimate_cache.write_text(
                est.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                estimate_engine.export_excel(
                    est, str(art_est / f"{key}__estimate.xlsx"))
                # Also write the user-facing alias
                (art_est / f"{drawing_id}__estimate.xlsx").write_bytes(
                    (art_est / f"{key}__estimate.xlsx").read_bytes())
            except Exception as e:
                return est, f"xlsx export failed: {e}"
            return est, None
        step, payload = _step("estimate", _estimate)
        if step.ok:
            est, warn = payload
            result.estimate = est
            step.detail = {"lines": len(est.lines), "total": est.total,
                           "currency": est.currency, "warn": warn}
        # Even on partial failure, persist what we have:
        if payload and not step.ok:
            result.estimate = payload
            step.detail = {"warn": step.note}
    result.steps.append(step)
    if result.estimate is not None:
        (art_est / f"{drawing_id}__estimate.json").write_text(
            result.estimate.model_dump_json(ensure_ascii=False, indent=2),
            encoding="utf-8")

    result.total_latency_s = round(time.time() - t_total, 2)
    result.total_tokens = {"input": total_in, "output": total_out,
                           "total": total_in + total_out,
                           "cache_hits": cache_hits,
                           "hash": img_h,
                           "key": key,
                           "hash_compute_s": round(hash_dt, 4)}
    return result
