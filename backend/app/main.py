"""FastAPI app exposing the qsmeta MVP to the React frontend.

Endpoints:
  GET  /api/health                     liveness
  GET  /api/drawings                   list known drawings
  POST /api/extract                    run two-pass on a known drawing
  POST /api/gaps                       run gap detector on a stored JSON
  POST /api/crop_reask                 crop a bbox and re-ask the cheap model
  POST /api/estimate                   derive estimate lines from a JSON
  POST /api/estimate_export            same + write Excel
  GET  /api/artifacts/{kind}/{name}    serve saved files (image, json, gap)
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import subprocess
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (config, extractor, gap_detector, crop_reask as crop_mod,
               estimate_engine, materials as materials_mod, pipeline)
from .schemas import (AugmentRequest, AugmentResponse, CropReaskRequest,
                      CropReaskResponse, DrawingJSON, EstimateResult, FindNullsRequest,
                      FindNullsResponse, GapReport, MaterialsRequest, MaterialsResponse,
                      MaterialsResult, NullLocation, Pass1Facts)

# ---------------------------------------------------------------------------
# App + CORS (React dev server lives on a different origin)
# ---------------------------------------------------------------------------

config.load_env()
app = FastAPI(title="qsmeta API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persist artefacts under qsmeta/artifacts/<drawing_id>/
ART = config.ARTIFACTS_DIR
ART.mkdir(parents=True, exist_ok=True)
(ART / "images").mkdir(exist_ok=True)
(ART / "json").mkdir(exist_ok=True)
(ART / "gaps").mkdir(exist_ok=True)
(ART / "crops").mkdir(exist_ok=True)
(ART / "estimates").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Request shapes
# ---------------------------------------------------------------------------

class ExtractRequest(BaseModel):
    image_path: str
    drawing_id: str | None = None
    model: str | None = None


class ExtractResponse(BaseModel):
    drawing_id: str
    pass1: Pass1Facts
    pass2: DrawingJSON
    pass1_meta: dict
    pass2_meta: dict
    image_path: str
    image_url: str


class GapRequest(BaseModel):
    drawing_id: str


class EstimateRequest(BaseModel):
    drawing_id: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "extraction_model": config.EXTRACTION_MODEL,
        "crop_model": config.CROPCROP_MODEL,
        "media_resolution": config.MEDIA_RESOLUTION,
    }


# ---------------------------------------------------------------------------
# Drawings (file-based discovery)
# ---------------------------------------------------------------------------

@app.get("/api/drawings")
def list_drawings():
    """Walk data_extracted/ for dataset folders and return their page1 paths."""
    out: list[dict] = []
    if not config.DATA_DIR.exists():
        return out
    for ds_dir in sorted(config.DATA_DIR.iterdir()):
        if not ds_dir.is_dir():
            continue
        images = ds_dir / "images"
        if not images.exists():
            continue
        for img in sorted(images.glob("*.png")):
            if ".resized." in img.name:
                continue
            out.append({
                "drawing_id": f"{ds_dir.name}__{img.stem}",
                "dataset": ds_dir.name,
                "filename": img.name,
                "image_path": str(img.resolve()),
                "image_url": f"/api/artifacts/image?path={img.resolve()}",
            })
    return out


# ---------------------------------------------------------------------------
# Upload PDF: split into pages, save PNGs, return drawing list entries
# ---------------------------------------------------------------------------

@app.post("/api/upload_pdf")
async def upload_pdf(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    pdf_bytes = await file.read()
    stem = Path(file.filename).stem
    safe_stem = re.sub(r"[^\w\-]", "_", stem)[:64]

    images_dir = config.DATA_DIR / safe_stem / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        # pdftoppm: -r 150 = 150 DPI, -png, output prefix = images_dir/page
        out_prefix = str(images_dir / "page")
        result = subprocess.run(
            ["pdftoppm", "-r", "150", "-png", tmp_path, out_prefix],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise HTTPException(500, f"pdftoppm error: {result.stderr.strip()}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    drawings: list[dict] = []
    for img_path in sorted(images_dir.glob("page-*.png")):
        drawings.append({
            "drawing_id": f"{safe_stem}__{img_path.stem}",
            "dataset": safe_stem,
            "filename": img_path.name,
            "image_path": str(img_path.resolve()),
            "image_url": f"/api/artifacts/image?path={img_path.resolve()}",
        })

    return {"dataset": safe_stem, "pages": len(drawings), "drawings": drawings}


# ---------------------------------------------------------------------------
# Extract: run the two-pass pipeline
# ---------------------------------------------------------------------------

@app.post("/api/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest):
    image_path = Path(req.image_path)
    if not image_path.exists():
        raise HTTPException(404, f"image not found: {image_path}")
    drawing_id = req.drawing_id or image_path.stem

    drawing, p2_meta, facts, p1_meta, p1_text, p2_text = extractor.run_two_pass(
        image_path, model=req.model,
    )

    # Persist artefacts.
    (ART / "json" / f"{drawing_id}__pass1.json").write_text(
        facts.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    (ART / "json" / f"{drawing_id}__pass2.json").write_text(
        drawing.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    (ART / "json" / f"{drawing_id}__pass1.txt").write_text(p1_text, encoding="utf-8")
    (ART / "json" / f"{drawing_id}__pass2.txt").write_text(p2_text, encoding="utf-8")

    return ExtractResponse(
        drawing_id=drawing_id,
        pass1=facts,
        pass2=drawing,
        pass1_meta=p1_meta,
        pass2_meta=p2_meta,
        image_path=str(image_path.resolve()),
        image_url=f"/api/artifacts/image?path={image_path.resolve()}",
    )


# ---------------------------------------------------------------------------
# Gaps: run the heuristic detector on a previously stored JSON
# ---------------------------------------------------------------------------

@app.post("/api/gaps", response_model=GapReport)
def gaps(req: GapRequest):
    json_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    if not json_path.exists():
        raise HTTPException(404, f"no pass2 JSON for drawing_id={req.drawing_id}")
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))

    # Recover image path from the same drawing_id stem.
    # We store it next to the JSON when we extract; for now, scan data_extracted.
    image_path = _find_image_for(req.drawing_id)
    if image_path is None:
        raise HTTPException(404, f"no source image for drawing_id={req.drawing_id}")
    w, h = extractor.image_size(image_path)
    report = gap_detector.detect_gaps(drawing, str(image_path), w, h)
    (ART / "gaps" / f"{req.drawing_id}__gaps.json").write_text(
        report.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Crop re-ask
# ---------------------------------------------------------------------------

@app.post("/api/crop_reask", response_model=CropReaskResponse)
def reask(req: CropReaskRequest):
    image_path = _find_image_for(req.drawing_id)
    if image_path is None:
        raise HTTPException(404, f"no source image for drawing_id={req.drawing_id}")
    return crop_mod.crop_reask(image_path, req.bbox_xyxy, req.field, req.hint)


# ---------------------------------------------------------------------------
# Augment: user drew a bbox; model fills all visible null fields in it
# ---------------------------------------------------------------------------

def _apply_patch(drawing: DrawingJSON, patches: list[dict]) -> tuple[DrawingJSON, list[str], list[dict]]:
    """Merge a [{path, value}, ...] patch into a DrawingJSON. Returns
    (new_drawing, applied_paths, skipped)."""
    applied: list[str] = []
    skipped: list[dict] = []
    raw = drawing.model_dump()
    for entry in patches:
        path = entry.get("path", "")
        value = entry.get("value")
        if not path or value is None:
            skipped.append({"path": path, "reason": "empty value or path"})
            continue
        # Walk dot/index path, e.g. "assemblies[0].layers_top_to_bottom[2].thickness_mm"
        cursor = raw
        tokens = re.split(r"\.|(?=\[)", path)
        tokens = [t for t in tokens if t]
        ok = True
        for i, t in enumerate(tokens):
            is_last = (i == len(tokens) - 1)
            m = re.match(r"^(\w+)\[(\d+)\]$", t)
            try:
                if m:
                    key, idx = m.group(1), int(m.group(2))
                    if not isinstance(cursor, dict) or key not in cursor:
                        ok = False; break
                    if is_last:
                        cursor[key][idx] = value
                    else:
                        cursor = cursor[key][idx]
                elif "[" in t and t.endswith("]"):
                    key, idx = t[:-1].split("[")
                    idx = int(idx)
                    if not isinstance(cursor, dict) or key not in cursor:
                        ok = False; break
                    if is_last:
                        cursor[key][idx] = value
                    else:
                        cursor = cursor[key][idx]
                else:
                    if not isinstance(cursor, dict) or t not in cursor:
                        ok = False; break
                    if is_last:
                        cursor[t] = value
                    else:
                        cursor = cursor[t]
            except (KeyError, IndexError, TypeError):
                ok = False; break
        if ok:
            applied.append(path)
        else:
            skipped.append({"path": path, "reason": "path not found or value type mismatch"})
    return DrawingJSON.model_validate(raw), applied, skipped


@app.post("/api/augment", response_model=AugmentResponse)
def augment(req: AugmentRequest):
    """User drew a bbox; model fills every null field visible in the crop."""
    image_path = _find_image_for(req.drawing_id)
    if image_path is None:
        raise HTTPException(404, f"no source image for drawing_id={req.drawing_id}")
    json_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    if not json_path.exists():
        raise HTTPException(404, f"no pass2 JSON for drawing_id={req.drawing_id}")
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))

    result = crop_mod.bbox_augment(image_path, req.bbox_xyxy, drawing, req.hint)
    patches = result.get("patches", [])
    new_drawing, applied, skipped = _apply_patch(drawing, patches)

    # Persist the merged JSON so the UI can re-fetch it.
    json_path.write_text(new_drawing.model_dump_json(ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return AugmentResponse(
        drawing_id=req.drawing_id,
        patches=patches,
        applied=applied,
        skipped=skipped,
        bbox=req.bbox_xyxy,
        raw=result.get("raw", ""),
        latency_s=result.get("latency_s", 0.0),
        tokens_in=result.get("tokens_in"),
        tokens_out=result.get("tokens_out"),
    )


# ---------------------------------------------------------------------------
# find_nulls: scan whole sheet, return bbox locations of all null fields
# ---------------------------------------------------------------------------

@app.post("/api/find_nulls", response_model=FindNullsResponse)
def find_nulls(req: FindNullsRequest):
    image_path = _find_image_for(req.drawing_id)
    if image_path is None:
        raise HTTPException(404, f"no source image for drawing_id={req.drawing_id}")
    json_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    if not json_path.exists():
        raise HTTPException(404, f"no pass2 JSON for drawing_id={req.drawing_id}")
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))

    result = crop_mod.find_nulls(image_path, drawing)
    return FindNullsResponse(
        drawing_id=req.drawing_id,
        locations=[NullLocation(**loc) for loc in result.get("locations", [])],
        image_size=result.get("image_size", [0, 0]),
        latency_s=result.get("latency_s", 0.0),
        tokens_in=result.get("tokens_in"),
        tokens_out=result.get("tokens_out"),
    )


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

@app.post("/api/estimate", response_model=EstimateResult)
def estimate(req: EstimateRequest):
    json_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    if not json_path.exists():
        raise HTTPException(404, f"no pass2 JSON for drawing_id={req.drawing_id}")
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))
    result = estimate_engine.estimate(drawing)
    (ART / "estimates" / f"{req.drawing_id}__estimate.json").write_text(
        result.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@app.post("/api/estimate_export")
def estimate_export(req: EstimateRequest):
    json_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    if not json_path.exists():
        raise HTTPException(404, f"no pass2 JSON for drawing_id={req.drawing_id}")
    drawing = DrawingJSON.model_validate_json(json_path.read_text(encoding="utf-8"))
    result = estimate_engine.estimate(drawing)
    xlsx_path = ART / "estimates" / f"{req.drawing_id}__estimate.xlsx"
    estimate_engine.export_excel(result, str(xlsx_path))
    return {"path": str(xlsx_path), "total": result.total, "currency": result.currency}


# ---------------------------------------------------------------------------
# Materials (colour pass): read spec tables + colour legend, then reconcile
# against the assembly layers from pass2.
# ---------------------------------------------------------------------------

@app.post("/api/materials", response_model=MaterialsResponse)
def materials(req: MaterialsRequest):
    image_path = Path(req.image_path) if req.image_path else _find_image_for(req.drawing_id)
    if image_path is None or not image_path.exists():
        raise HTTPException(404, f"no source image for drawing_id={req.drawing_id}")

    mat_path = ART / "json" / f"{req.drawing_id}__materials.json"
    if not req.force and mat_path.exists():
        mats = MaterialsResult.model_validate_json(mat_path.read_text(encoding="utf-8"))
    else:
        mats = materials_mod.extract_materials(image_path, model=req.model)
        mats.drawing_id = req.drawing_id
        mat_path.write_text(mats.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")

    # Reconcile against pass2 assemblies if we have them.
    reconcile = None
    p2_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    if p2_path.exists():
        drawing = DrawingJSON.model_validate_json(p2_path.read_text(encoding="utf-8"))
        reconcile = materials_mod.reconcile(drawing, mats)
        (ART / "json" / f"{req.drawing_id}__reconcile.json").write_text(
            reconcile.model_dump_json(ensure_ascii=False, indent=2), encoding="utf-8")

    return MaterialsResponse(drawing_id=req.drawing_id, materials=mats, reconcile=reconcile)


# ---------------------------------------------------------------------------
# Full cycle: extract → gaps → autofill → re-gaps → estimate → xlsx
# ---------------------------------------------------------------------------

class FullCycleRequest(BaseModel):
    image_path: str
    drawing_id: str | None = None
    model: str | None = None
    crop_model: str | None = None
    max_auto_reask: int = 6
    force: bool = False


class FullCycleResponse(BaseModel):
    drawing_id: str
    image_path: str
    steps: list[dict]
    total_latency_s: float
    total_tokens: dict
    pass1: dict
    pass2: dict
    pass3: dict | None = None
    auto_detect: list[dict] = []
    gap_report: dict | None
    final_gaps: dict | None
    estimate: dict | None
    xlsx_path: str | None
    error: str | None = None


@app.post("/api/full_cycle", response_model=FullCycleResponse)
def full_cycle(req: FullCycleRequest):
    image_path = Path(req.image_path)
    if not image_path.exists():
        raise HTTPException(404, f"image not found: {image_path}")
    res = pipeline.run_full_cycle(
        image_path,
        drawing_id=req.drawing_id,
        model=req.model,
        crop_model=req.crop_model,
        max_auto_reask=req.max_auto_reask,
        force=req.force,
    )
    xlsx = (config.ARTIFACTS_DIR / "estimates" / f"{res.drawing_id}__estimate.xlsx")
    return FullCycleResponse(
        drawing_id=res.drawing_id,
        image_path=res.image_path,
        steps=[asdict(s) for s in res.steps],
        total_latency_s=res.total_latency_s,
        total_tokens=res.total_tokens,
        pass1=res.pass1.model_dump() if res.pass1 else {},
        pass2=res.pass2.model_dump() if res.pass2 else {},
        pass3=res.pass3,
        auto_detect=res.auto_detect,
        gap_report=res.gap_report.model_dump() if res.gap_report else None,
        final_gaps=res.final_gaps.model_dump() if res.final_gaps else None,
        estimate=res.estimate.model_dump() if res.estimate else None,
        xlsx_path=str(xlsx) if xlsx.exists() else None,
        error=res.error,
    )


# ---------------------------------------------------------------------------
# Pass3 endpoint — read-only view of the merged drawing
# ---------------------------------------------------------------------------

@app.post("/api/pass3")
def get_pass3(req: EstimateRequest):
    """Read the merged pass3 JSON (or fall back to pass2 if no merge was done)."""
    p3_path = ART / "json" / f"{req.drawing_id}__pass3.json"
    p2_path = ART / "json" / f"{req.drawing_id}__pass2.json"
    p1_path = ART / "json" / f"{req.drawing_id}__pass1.json"
    p3_merge = ART / "gaps" / f"{req.drawing_id}__pass3.json"
    if not p3_path.exists() and not p2_path.exists():
        raise HTTPException(404, f"no extract for drawing_id={req.drawing_id}")
    primary = p3_path if p3_path.exists() else p2_path
    drawing = DrawingJSON.model_validate_json(primary.read_text(encoding="utf-8"))
    pass1 = Pass1Facts.model_validate_json(p1_path.read_text(encoding="utf-8")) if p1_path.exists() else None
    merge_meta = json.loads(p3_merge.read_text(encoding="utf-8")) if p3_merge.exists() else None
    return {
        "drawing": drawing.model_dump(),
        "pass1_facts": pass1.model_dump() if pass1 else None,
        "merge": merge_meta,
    }


# ---------------------------------------------------------------------------
# Artefact serving (read-only images for the UI)
# ---------------------------------------------------------------------------

@app.get("/api/artifacts/image")
def serve_image(path: str):
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "image not found")
    # CORS-friendly inline: return base64.
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"path": str(p), "mime": "image/png", "data": data}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_image_for(drawing_id: str) -> Path | None:
    """Resolve drawing_id back to a file on disk.

    drawing_id format: '<dataset_dir>__<filename_stem>' (see /api/drawings).
    """
    if "__" in drawing_id:
        ds, stem = drawing_id.split("__", 1)
        candidate = config.DATA_DIR / ds / "images" / f"{stem}.png"
        if candidate.exists():
            return candidate
    # Fallback: glob any png with that stem
    for p in config.DATA_DIR.rglob(f"{drawing_id}*.png"):
        if ".resized." not in p.name:
            return p
    return None
