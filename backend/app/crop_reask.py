"""Crop a bbox region out of a drawing and ask the cheap model to fill
fields. Two modes:
  1. `crop_reask` — user has drawn a bbox; model fills ONE named field.
  2. `bbox_augment` — user drew a bbox; model finds ALL null fields in
     the current DrawingJSON that are visible inside the bbox, and
     returns them as a patch {path: value, ...}.
  3. `find_nulls` — user has NO bbox; model scans the whole sheet and
     locates where each null field is, returning bbox_xyxy per field.
"""
from __future__ import annotations

import base64
import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

from . import config
from .extractor import call_gemini, extract_json
from .schemas import CropReaskResponse, DrawingJSON, NullLocation

# Per-field prompt templates. Kept tiny and surgical — the cheap model only
# has to read ONE thing in a tiny image.
FIELD_PROMPTS: dict[str, str] = {
    "thickness_mm": (
        "Look at the leader line next to the material '{material}'. "
        "Return the thickness in millimetres as a plain integer, or null "
        "if not readable. JSON: {{\"thickness_mm\": <int|null>}}"
    ),
    "spans_mm": (
        "Read the dimension chain shown. Return each segment as "
        "\"<axis1>-<axis2>\": <int_mm>. JSON: {{\"spans\": {{...}}}}"
    ),
    "parapet_top": (
        "Find the highest elevation mark that points to a wall extension "
        "above the roof level (parapet). Return its value as written, "
        "e.g. '+4.550'. JSON: {{\"value\": \"<str|null>\"}}"
    ),
    "project_code": (
        "Read the project code from the title block, e.g. '341-4-AS'. "
        "JSON: {{\"value\": \"<str|null>\"}}"
    ),
}


def crop_with_padding(image_path: Path, bbox_xyxy: list[int],
                      pad_px: int | None = None) -> bytes:
    """Cut [x1, y1, x2, y2] with `pad_px` of padding on every side, return PNG bytes."""
    pad = pad_px if pad_px is not None else config.CROP_PAD_PX
    x1, y1, x2, y2 = bbox_xyxy
    with Image.open(image_path) as im:
        w, h = im.size
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        crop = im.crop((x1, y1, x2, y2))
        from io import BytesIO
        buf = BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue(), (x1, y1, x2, y2)


def crop_reask(image_path: Path, bbox_xyxy: list[int], field: str,
               hint: str | None = None,
               model: str | None = None) -> CropReaskResponse:
    """Crop + ask the cheap model for one field's value."""
    model = model or config.CROPCROP_MODEL
    crop_bytes, adjusted = crop_with_padding(image_path, bbox_xyxy)

    template = FIELD_PROMPTS.get(field)
    if not template:
        # Generic fallback.
        template = (
            f"Read the value for the field '{field}' from this drawing region. "
            "Return JSON: {\"value\": <int|str|number|null>}"
        )
    material = ""
    if hint and "material=" in hint:
        material = hint.split("material=", 1)[1].split(";", 1)[0]
    prompt = template.format(material=material) if "{material}" in template else template
    if hint and "{material}" not in template:
        prompt += f"\nContext: {hint}"

    text, p, o, dt = call_gemini(model, image_path, prompt)
    # Note: call_gemini encodes the WHOLE image again. For a real MVP we want
    # to feed the crop bytes directly. We do that here inline.
    api_key = config.get_gemini_key()
    if api_key:
        from google import genai
        from google.genai import types as genai_types
        client = genai.Client(api_key=api_key)
        from io import BytesIO
        resp = client.models.generate_content(
            model=model,
            contents=[
                genai_types.Part.from_bytes(data=crop_bytes, mime_type="image/png"),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=512,
                media_resolution="MEDIA_RESOLUTION_HIGH",
            ),
        )
        text = (resp.text or "").strip()
        p = getattr(resp.usage_metadata, "prompt_token_count", p)
        o = getattr(resp.usage_metadata, "candidates_token_count", o)

    parsed, err = extract_json(text)
    value: Any = None
    confidence = "medium"
    if parsed:
        # Unwrap common shapes.
        if field in parsed:
            value = parsed[field]
        elif "value" in parsed:
            value = parsed["value"]
        elif "spans" in parsed and field == "spans_mm":
            value = parsed["spans"]
        if "confidence" in parsed:
            confidence = parsed["confidence"]
    return CropReaskResponse(
        field=field,
        value=value,
        raw=text,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Two richer modes that work with the user's manual bbox selection
# ---------------------------------------------------------------------------

_AUGMENT_PROMPT = """You are given a CROPPED region of an architectural drawing,
together with the current JSON for the whole sheet. The JSON has many fields
that are still null because the model that produced the JSON couldn't see
the value on the full sheet.

Your job: ONLY look at the cropped image, and fill in any null fields whose
value is clearly visible in this crop. If a value is not visible, skip it.

Output JSON in this exact shape:
{
  "patches": [
    {"path": "<dot.path.into.json>", "value": <json value>},
    ...
  ]
}

Examples of paths:
  "elevations.parapet_top"
  "elevations.roof_top"
  "axes.building_dimensions_mm.length"
  "axes.spans_mm.1-2"
  "assemblies[0].layers_top_to_bottom[2].thickness_mm"
  "title_block.project_code"
  "computed.parapet_height_mm"
  "computed.parapet_length_mm"

If nothing is fillable, return {"patches": []}.
Return ONLY the JSON object, no commentary, no markdown fences.

CURRENT JSON (with nulls to fill):
{json}

HINT: {hint}
"""


def _call_gemini_with_crop(model: str, crop_bytes: bytes, prompt: str) -> tuple[str, int | None, int | None, float]:
    """Call Gemini with a CROPPED image, not the full sheet. Returns (text, in_tok, out_tok, dt)."""
    import time
    api_key = config.get_gemini_key()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    from google import genai
    from google.genai import types as genai_types
    client = genai.Client(api_key=api_key)
    t0 = time.time()
    resp = client.models.generate_content(
        model=model,
        contents=[
            genai_types.Part.from_bytes(data=crop_bytes, mime_type="image/png"),
            prompt,
        ],
        config=genai_types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=1024,
            media_resolution="MEDIA_RESOLUTION_HIGH",
        ),
    )
    dt = time.time() - t0
    text = (resp.text or "").strip()
    p = getattr(resp.usage_metadata, "prompt_token_count", None)
    o = getattr(resp.usage_metadata, "candidates_token_count", None)
    return text, p, o, dt


def bbox_augment(image_path: Path, bbox_xyxy: list[int], drawing: DrawingJSON,
                 hint: str = "", model: str | None = None) -> dict[str, Any]:
    """Take the user's bbox, look at the cropped region, and return a
    patch {path: value, ...} for any null fields the model can read.

    The caller is responsible for merging the patch into the drawing.
    """
    model = model or config.CROPCROP_MODEL
    crop_bytes, _ = crop_with_padding(image_path, bbox_xyxy)
    # Trim the JSON to just the null fields — model doesn't need everything.
    null_paths = _collect_null_paths(drawing)
    if not null_paths:
        return {"patches": [], "note": "no nulls in drawing"}
    skeleton = {p: None for p in null_paths}
    prompt = _AUGMENT_PROMPT.replace("{json}", json.dumps(skeleton, ensure_ascii=False, indent=2)) \
                            .replace("{hint}", hint or "(none)")
    text, p, o, dt = _call_gemini_with_crop(model, crop_bytes, prompt)
    parsed, err = extract_json(text)
    out = {
        "patches": parsed.get("patches", []) if parsed else [],
        "raw": text,
        "model": model,
        "bbox": bbox_xyxy,
        "tokens_in": p,
        "tokens_out": o,
        "latency_s": round(dt, 2),
    }
    if err:
        out["parse_error"] = err
    return out


_FIND_NULLS_PROMPT = """You are given a FULL architectural drawing and the
current JSON for that drawing. The JSON has null fields that need to be
filled. For EACH null field, locate it on the sheet and return its bounding
box in image pixel coordinates [x1, y1, x2, y2] (x and y in PIXEL
coordinates of the {w}x{h} image, NOT normalized 0-1000) where the value
lives. Bounding box should tightly wrap the text/annotation, not the
whole arrow.

Output JSON in this exact shape:
{
  "locations": [
    {
      "path": "<dot.path.into.json>",
      "bbox_xyxy": [x1, y1, x2, y2],
      "value_hint": "<what you see there, in 5-10 words, or null if unreadable>",
      "confidence": "high|medium|low"
    },
    ...
  ]
}

Paths to find:
{paths}

Image is {w}x{h} pixels; bbox must be within these bounds (0..{w} for x, 0..{h} for y).

For fields that are TRULY not on this sheet (e.g. parapet length on a
facade-only sheet), return bbox_xyxy=null and confidence="low".

Return ONLY the JSON object, no commentary, no markdown fences.
"""


def find_nulls(image_path: Path, drawing: DrawingJSON,
               model: str | None = None) -> dict[str, Any]:
    """Scan the full sheet and return bbox locations for every null field.

    Returns: {locations: [{path, bbox_xyxy, value_hint, confidence}], ...}
    """
    model = model or config.CROPCROP_MODEL
    null_paths = _collect_null_paths(drawing)
    if not null_paths:
        return {"locations": [], "note": "no nulls in drawing"}
    w, h = Image.open(image_path).size
    paths_list = "\n".join(f"  - {p}" for p in null_paths)
    prompt = _FIND_NULLS_PROMPT.replace("{paths}", paths_list) \
                               .replace("{w}", str(w)) \
                               .replace("{h}", str(h))
    text, p, o, dt = _call_gemini_with_crop(model, image_path.read_bytes(), prompt)
    parsed, err = extract_json(text)
    out = {
        "locations": parsed.get("locations", []) if parsed else [],
        "raw": text,
        "model": model,
        "image_size": [w, h],
        "tokens_in": p,
        "tokens_out": o,
        "latency_s": round(dt, 2),
    }
    if err:
        out["parse_error"] = err
    return out


def _collect_null_paths(obj: Any, path: str = "") -> list[str]:
    """Walk a JSON-like object and return dot-paths to every null leaf.

    Skips 'note' fields (free text, not measurements). Skips empty arrays/dicts
    that are placeholder defaults.
    """
    out: list[str] = []
    if obj is None:
        if path:
            out.append(path)
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("note", "where", "labels", "points_to", "side",
                     "elevation_arithmetic", "title", "object", "organization",
                     "stage", "sheet", "drawing_id", "value_hint"):
                continue
            out.extend(_collect_null_paths(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_collect_null_paths(v, f"{path}[{i}]"))
    return out
