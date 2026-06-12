"""Pydantic schemas shared by the API and the pipeline.

These are the contract between the Python backend and the React frontend.
Don't change field names without updating web/src/types.ts in lockstep.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pass 1 (grounding) — model just lists what it sees, no interpretation.
# ---------------------------------------------------------------------------

class ElevationFact(BaseModel):
    value: str = Field(..., description="As written on the drawing, e.g. '+4.550'")
    points_to: str = Field(..., description="Physical description of what the arrow indicates")
    side: str = Field("n/a", description="left|right|top|bottom|n/a")
    confidence: str = Field("medium", description="high|medium|low")


class DimensionFact(BaseModel):
    value_mm: int
    between: str = Field(..., description="What is being measured")
    confidence: str = "medium"


class NodeMarkerFact(BaseModel):
    marker: str
    where: str
    labels: Optional[str] = None


class Pass1Facts(BaseModel):
    elevations: list[ElevationFact] = []
    dimensions: list[DimensionFact] = []
    node_markers: list[NodeMarkerFact] = []


# ---------------------------------------------------------------------------
# Pass 2 (semantic) — final per-drawing JSON.
# ---------------------------------------------------------------------------

class AxisSet(BaseModel):
    horizontal: Optional[list[str]] = None
    vertical: Optional[list[str]] = None
    spans_mm: dict[str, int] = Field(default_factory=dict)
    building_dimensions_mm: dict[str, Optional[int]] = Field(
        default_factory=lambda: {"length": None, "width": None, "height": None}
    )


class ElevationBlock(BaseModel):
    ground: Optional[str] = None
    floor_1: Optional[str] = None
    floor_2: Optional[str] = None
    parapet_top: Optional[str] = None
    ridge: Optional[str] = None
    other: list[str] = []


class AssemblyLayer(BaseModel):
    material: str
    thickness_mm: Optional[int] = None
    note: Optional[str] = None


class Assembly(BaseModel):
    id: str
    type: Optional[str] = None
    layers_top_to_bottom: list[AssemblyLayer] = []


class Quantity(BaseModel):
    name: str
    value: float
    unit: str


class TitleBlock(BaseModel):
    project_code: Optional[str] = None
    stage: Optional[str] = None
    sheet: Optional[str] = None
    organization: Optional[str] = None
    object: Optional[str] = None


class ComputedBlock(BaseModel):
    parapet_height_mm: Optional[int] = None
    parapet_length_mm: Optional[int] = None
    elevation_arithmetic: Optional[str] = None


class DrawingJSON(BaseModel):
    """Top-level final output for one drawing page."""
    drawing_id: Optional[str] = None
    title: Optional[str] = None
    axes: AxisSet = Field(default_factory=AxisSet)
    elevations: ElevationBlock = Field(default_factory=ElevationBlock)
    assemblies: list[Assembly] = []
    node_marks: list[str] = []
    node_marks_referenced: dict[str, Optional[str]] = Field(default_factory=dict)
    cross_references: list[dict[str, str]] = []
    quantities: list[Quantity] = []
    title_block: TitleBlock = Field(default_factory=TitleBlock)
    computed: ComputedBlock = Field(default_factory=ComputedBlock)


# ---------------------------------------------------------------------------
# Gap report — what the model missed.
# ---------------------------------------------------------------------------

class GapItem(BaseModel):
    """One hole in the extracted JSON.

    `bbox_xyxy` is in original-image pixel coordinates so the React UI can
    draw the red rectangle without further conversion.
    """
    field: str = Field(..., description="Dot path into DrawingJSON, e.g. 'assemblies[2].layers_top_to_bottom[0].thickness_mm'")
    reason: str = Field(..., description="Why this is flagged: 'null' | 'low_confidence' | 'missing_derivation'")
    severity: str = Field("high", description="high|medium|low")
    bbox_xyxy: Optional[list[int]] = Field(None, description="[x1, y1, x2, y2] in image pixels, or null if unknown")
    suggestion: Optional[str] = Field(None, description="What to do about it (e.g. 'crop_assembly_id_2')")


class GapReport(BaseModel):
    drawing_id: Optional[str] = None
    image_path: Optional[str] = None
    image_width: int = 0
    image_height: int = 0
    gaps: list[GapItem] = []
    total_score: float = Field(0.0, description="0..1 — fraction of fields populated")


# ---------------------------------------------------------------------------
# Re-ask result — what the cheap model says about a single cropped region.
# ---------------------------------------------------------------------------

class CropReaskRequest(BaseModel):
    drawing_id: str
    field: str
    bbox_xyxy: list[int]
    hint: Optional[str] = None


class CropReaskResponse(BaseModel):
    field: str
    value: Any = None
    raw: str
    confidence: str = "medium"


class AugmentRequest(BaseModel):
    image_path: str
    drawing_id: str
    bbox_xyxy: list[int]
    hint: str = ""


class AugmentResponse(BaseModel):
    drawing_id: str
    patches: list[dict]  # [{path, value}, ...]
    applied: list[str]   # which paths were actually applied
    skipped: list[dict]  # [{path, reason}]
    bbox: list[int]
    raw: str = ""
    latency_s: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None


class FindNullsRequest(BaseModel):
    image_path: str
    drawing_id: str


class NullLocation(BaseModel):
    path: str
    bbox_xyxy: list[int] | None = None
    value_hint: str | None = None
    confidence: str = "low"


class FindNullsResponse(BaseModel):
    drawing_id: str
    locations: list[NullLocation]
    image_size: list[int]
    latency_s: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None


# ---------------------------------------------------------------------------
# Estimate engine output.
# ---------------------------------------------------------------------------

class EstimateLine(BaseModel):
    gesn_code: str
    description: str
    unit: str
    quantity: float
    unit_price: Optional[float] = None
    total: Optional[float] = None
    source_fields: list[str] = Field(default_factory=list, description="Which JSON fields drove this line")


class EstimateResult(BaseModel):
    drawing_id: Optional[str] = None
    lines: list[EstimateLine] = []
    total: float = 0.0
    currency: str = "RUB"


# ---------------------------------------------------------------------------
# Materials pass (the colour pass) — reads specification tables AND maps
# colour-coded hatching to materials via the legend. Runs on the COLOUR
# image (no BGR2Gray), see materials.py.
# ---------------------------------------------------------------------------

class MaterialItem(BaseModel):
    """One material/position from a spec table or colour legend."""
    name: str = Field(..., description="Material name as written on the sheet")
    position: Optional[str] = Field(None, description="Поз. / item number if any")
    unit: Optional[str] = Field(None, description="Ед. изм.: м2|м3|м.п.|шт|кг|т")
    quantity: Optional[float] = Field(None, description="Кол-во if present in table")
    color_hex: Optional[str] = Field(None, description="Legend colour as #RRGGBB, if hatching is colour-coded")
    source: str = Field("table", description="table|color|both — where it came from")
    note: Optional[str] = None


class MaterialsResult(BaseModel):
    drawing_id: Optional[str] = None
    materials: list[MaterialItem] = []
    tables_found: int = 0
    legend_found: bool = False
    raw: str = ""
    latency_s: float = 0.0
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None


class MaterialsRequest(BaseModel):
    drawing_id: str
    image_path: Optional[str] = None
    model: Optional[str] = None
    force: bool = False


# ---------------------------------------------------------------------------
# Reconciliation — assembly layers (pass2) vs the specification list.
# ---------------------------------------------------------------------------

class ReconcileItem(BaseModel):
    name: str = Field(..., description="Canonical material name")
    status: str = Field(..., description="matched|only_in_assembly|only_in_spec")
    in_assembly: bool = False
    in_spec: bool = False
    matched_to: Optional[str] = Field(None, description="Counterpart name it matched, if status=matched")
    spec_unit: Optional[str] = None
    spec_quantity: Optional[float] = None
    assembly_refs: list[str] = Field(default_factory=list, description="assembly ids that use this material")


class ReconcileReport(BaseModel):
    drawing_id: Optional[str] = None
    items: list[ReconcileItem] = []
    matched: int = 0
    only_in_assembly: int = 0
    only_in_spec: int = 0


class MaterialsResponse(BaseModel):
    drawing_id: str
    materials: MaterialsResult
    reconcile: Optional[ReconcileReport] = None
