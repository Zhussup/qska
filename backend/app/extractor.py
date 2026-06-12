"""Two-pass Gemini extractor: grounding pass + semantic pass.

Reused by both the FastAPI app and the standalone CLI.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from google import genai
from google.genai import types as genai_types
from PIL import Image

from . import config
from .schemas import DrawingJSON, Pass1Facts

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SCHEMA_HINT = """{
  "drawing_id": "string or null",
  "title": "string or null",
  "axes": {
    "horizontal": ["list of axis labels (str) or null"],
    "vertical":   ["list of axis labels (str) or null"],
    "spans_mm":   {"<axis1>-<axis2>": <int mm>},
    "building_dimensions_mm": {"length": <int|null>, "width": <int|null>, "height": <int|null>}
  },
  "elevations": {
    "ground": "str|null", "floor_1": "str|null", "floor_2": "str|null",
    "parapet_top": "str|null", "ridge": "str|null",
    "other": ["any other elevations found on the sheet"]
  },
  "assemblies": [
    {
      "id": "section or node name, e.g. '1-1' or 'AC-3'",
      "type": "krovlya|wall|floor|perekrytie|uzel|fasad|null",
      "layers_top_to_bottom": [{"material": "str", "thickness_mm": <int|null>, "note": "str|null"}]
    }
  ],
  "node_marks": ["all node markers, e.g. ['1','2','AC-3','AC-14']"],
  "node_marks_referenced": {"<marker>": "short description or sheet ref, or null"},
  "cross_references": [{"item": "str", "sheet": "str e.g. 'AC-7'"}],
  "quantities": [{"name": "str", "value": <number>, "unit": "m2|mp|sht|m3"}],
  "title_block": {
    "project_code": "str|null", "stage": "str|null", "sheet": "str|null",
    "organization": "str|null", "object": "str|null"
  },
  "computed": {
    "parapet_height_mm": "<int|null>",
    "parapet_length_mm": "<int|null>",
    "elevation_arithmetic": "str|null — show the subtraction you did"
  }
}"""

PROMPT_PASS1 = """You are reading ONE scanned architectural/construction drawing.
Your job: READ everything numeric and label WHAT each value points at.
Do NOT interpret, do NOT compute, do NOT invent.

Step 1. Find ALL elevation marks (format: ±0.000, +4.550, -0.030, etc.).
For EACH mark return:
  {"value": "as written, e.g. '+4.550'",
   "points_to": "physical description of what the arrow indicates
                 (e.g. 'top of wall above roof', 'top of roof membrane',
                 'ground level', 'top of window opening', 'bottom of beam').
                 DO NOT use abstract terms like 'parapet' or 'ridge' —
                 describe PHYSICALLY what element is at the arrow tip.",
   "side": "left|right|top|bottom|n/a",
   "confidence": "high|medium|low"}

Step 2. Find ALL dimension chains (mm). For EACH return:
  {"value_mm": <int>, "between": "what it measures",
   "confidence": "high|medium|low"}

Step 3. Find ALL node markers (1, 2, 3, AC-3, AC-14, K1, Poz.5, etc.).
For EACH return:
  {"marker": "str", "where": "where on the sheet",
   "labels": "nearby callout text if any"}

RULES:
- List EVERYTHING you see, including small annotations.
- If unsure → confidence: "low", but still record.
- Never invent numbers. If unreadable, skip it.
- Return ONLY valid JSON, no markdown fences, no commentary.

Schema:
{
  "elevations": [...],
  "dimensions": [...],
  "node_markers": [...]
}"""

PROMPT_PASS2 = """You are given (a) a drawing and (b) the JSON facts extracted from it
during the first pass. Apply the SEMANTIC MAPPING below to produce the
final estimate-friendly JSON.

Logic (apply in order):
1. PARAPET = horizontal wall extension ABOVE roof level. Look in elevations
   for entries described as 'top of wall above roof' / 'top of parapet wall'
   / 'top of wall higher than roof'. That → parapet_top.
   The matching 'top of roof membrane' / 'top of roof' / 'top of waterproofing'
   entry → roof_top (kept in elevations.other).
   height_parapet_mm = (parapet_top − roof_top) expressed in mm.
2. RIDGE = top intersection of sloped roof. If there is a unique peak
   elevation above parapet_top, that is ridge.
3. GROUND = the entry with value ±0.000 or marked 'ground level'.
4. PARAPET LENGTH = sum of horizontal axis spans belonging to the same facade.
5. For every node marker from node_markers, copy to assemblies[].id and
   look up its labels to fill layers_top_to_bottom.

Return the final JSON using exactly this schema:
""" + SCHEMA_HINT + """

INPUT FACTS (from pass 1):
"""


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _load_resized_rgb(path: Path, max_side: int = 2048) -> np.ndarray:
    """Open an image, downscale so its longest side <= max_side, return RGB ndarray."""
    img = Image.open(path).convert("RGB")
    img.load()
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.array(img)


def encode_image(path: Path, grayscale: bool = True) -> tuple[bytes, str]:
    """Encode a drawing as PNG bytes for the VLM.

    grayscale=True (default for pass1/pass2): RGB → BGR → GRAY via OpenCV.
      Architectural line-work loses nothing in gray and the smaller payload
      keeps token cost down.
    grayscale=False (materials/color pass): keep full colour, because the
      material legend and hatching are colour-coded — see materials.py.
    """
    arr = _load_resized_rgb(path)
    if grayscale:
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        out = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    else:
        # cv2.imencode expects BGR; convert from PIL's RGB so colours stay true.
        out = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        raise RuntimeError(f"cv2.imencode failed for {path}")
    return bytes(buf), "image/png"


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


# ---------------------------------------------------------------------------
# JSON extraction (tolerate markdown fences, leading prose)
# ---------------------------------------------------------------------------

JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> tuple[dict | None, str | None]:
    if not text:
        return None, "empty response"
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError:
        pass
    m = JSON_BLOCK.search(cleaned)
    if m:
        try:
            return json.loads(m.group(0)), None
        except json.JSONDecodeError as e:
            return None, f"json decode error: {e}"
    return None, "no JSON block found"


# ---------------------------------------------------------------------------
# Gemini call wrapper (pass-aware)
# ---------------------------------------------------------------------------

def _build_image_part(img_bytes: bytes, mime: str) -> genai_types.Part:
    return genai_types.Part.from_bytes(data=img_bytes, mime_type=mime)


def call_gemini(model: str, image_path: Path, prompt: str,
                grayscale: bool = True) -> tuple[str, int | None, int | None, float]:
    """Single Gemini request. Returns (text, prompt_tokens, output_tokens, latency_s)."""
    api_key = config.get_gemini_key()
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    client = genai.Client(api_key=api_key)
    img_bytes, mime = encode_image(image_path, grayscale=grayscale)
    t0 = time.time()
    resp = client.models.generate_content(
        model=model,
        contents=[_build_image_part(img_bytes, mime), prompt],
        config=genai_types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=8192,
            media_resolution=config.MEDIA_RESOLUTION,
        ),
    )
    dt = time.time() - t0
    text = (resp.text or "").strip()
    p_tokens = getattr(resp.usage_metadata, "prompt_token_count", None)
    o_tokens = getattr(resp.usage_metadata, "candidates_token_count", None)
    return text, p_tokens, o_tokens, dt


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _denormalize_bbox(bbox_0_1000: list[int], w: int, h: int) -> list[int]:
    """Convert Gemini-style [ymin, xmin, ymax, xmax] (0..1000) to image pixels
    in [x1, y1, x2, y2] (x/y swapped into our order)."""
    if not bbox_0_1000 or len(bbox_0_1000) != 4:
        return bbox_0_1000
    y1, x1, y2, x2 = bbox_0_1000
    return [
        max(0, min(w, int(round(x1 / 1000 * w)))),
        max(0, min(h, int(round(y1 / 1000 * h)))),
        max(0, min(w, int(round(x2 / 1000 * w)))),
        max(0, min(h, int(round(y2 / 1000 * h)))),
    ]


def run_pass1(image_path: Path, model: str | None = None) -> tuple[Pass1Facts, dict]:
    """Run the grounding pass. Returns (parsed facts, run_meta).

    Note: Pass 1 deliberately does NOT ask Gemini for bbox_xyxy. VLM-estimated
    coordinates are noisy (~30-50% off), and post-hoc conversion 0-1000 →
    pixels adds quantisation error. We get the text facts and trust the user
    (via crop_reask) to localise anything we need to look at again.
    """
    model = model or config.EXTRACTION_MODEL
    text, p, o, dt = call_gemini(model, image_path, PROMPT_PASS1)
    parsed, _ = extract_json(text)
    if parsed is None:
        facts = Pass1Facts()
    else:
        try:
            facts = Pass1Facts.model_validate(parsed)
        except Exception:
            facts = Pass1Facts()

    meta = {
        "model": model, "pass": 1,
        "latency_s": round(dt, 2),
        "prompt_tokens": p, "output_tokens": o,
        "raw_text_path": None,
    }
    return facts, meta, text


def run_pass2(image_path: Path, facts: Pass1Facts,
              model: str | None = None) -> tuple[DrawingJSON, dict, str]:
    """Run the semantic pass. Pass-1 facts are injected into the prompt."""
    model = model or config.EXTRACTION_MODEL
    facts_text = facts.model_dump_json(ensure_ascii=False, indent=2)
    prompt = PROMPT_PASS2 + facts_text
    text, p, o, dt = call_gemini(model, image_path, prompt)
    parsed, _ = extract_json(text)
    if parsed is None:
        drawing = DrawingJSON()
    else:
        try:
            drawing = DrawingJSON.model_validate(parsed)
        except Exception:
            # Try a partial parse: keep whatever schema fields validate.
            try:
                drawing = DrawingJSON.model_validate({k: v for k, v in parsed.items()
                                                    if k in DrawingJSON.model_fields})
            except Exception:
                drawing = DrawingJSON()
    meta = {
        "model": model, "pass": 2,
        "latency_s": round(dt, 2),
        "prompt_tokens": p, "output_tokens": o,
    }
    return drawing, meta, text


def run_two_pass(image_path: Path, model: str | None = None
                 ) -> tuple[DrawingJSON, dict, Pass1Facts, dict, str, str]:
    """Run both passes. Returns (final_json, pass2_meta, pass1_facts, pass1_meta, p1_text, p2_text)."""
    facts, p1_meta, p1_text = run_pass1(image_path, model)
    drawing, p2_meta, p2_text = run_pass2(image_path, facts, model)
    return drawing, p2_meta, facts, p1_meta, p1_text, p2_text
