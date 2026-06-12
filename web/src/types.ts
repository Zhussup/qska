// Mirrors backend/app/schemas.py. If you change one, change the other.

export interface AxisSet {
  horizontal: string[] | null;
  vertical: string[] | null;
  spans_mm: Record<string, number>;
  building_dimensions_mm: {
    length: number | null;
    width: number | null;
    height: number | null;
  };
}

export interface ElevationBlock {
  ground: string | null;
  floor_1: string | null;
  floor_2: string | null;
  parapet_top: string | null;
  ridge: string | null;
  other: string[];
}

export interface AssemblyLayer {
  material: string;
  thickness_mm: number | null;
  note: string | null;
}

export interface Assembly {
  id: string;
  type: string | null;
  layers_top_to_bottom: AssemblyLayer[];
}

export interface Quantity {
  name: string;
  value: number;
  unit: string;
}

export interface TitleBlock {
  project_code: string | null;
  stage: string | null;
  sheet: string | null;
  organization: string | null;
  object: string | null;
}

export interface ComputedBlock {
  parapet_height_mm: number | null;
  parapet_length_mm: number | null;
  elevation_arithmetic: string | null;
}

export interface DrawingJSON {
  drawing_id: string | null;
  title: string | null;
  axes: AxisSet;
  elevations: ElevationBlock;
  assemblies: Assembly[];
  node_marks: string[];
  node_marks_referenced: Record<string, string | null>;
  cross_references: { item: string; sheet: string }[];
  quantities: Quantity[];
  title_block: TitleBlock;
  computed: ComputedBlock;
}

export interface ElevationFact {
  value: string;
  points_to: string;
  side: string;
  confidence: string;
  bbox_xyxy: number[] | null;
}

export interface DimensionFact {
  value_mm: number;
  between: string;
  confidence: string;
  bbox_xyxy: number[] | null;
}

export interface NodeMarkerFact {
  marker: string;
  where: string;
  labels: string | null;
  bbox_xyxy: number[] | null;
}

export interface Pass1Facts {
  elevations: ElevationFact[];
  dimensions: DimensionFact[];
  node_markers: NodeMarkerFact[];
}

export interface AugmentResponse {
  drawing_id: string;
  patches: { path: string; value: unknown }[];
  applied: string[];
  skipped: { path: string; reason: string }[];
  bbox: number[];
  raw: string;
  latency_s: number;
  tokens_in: number | null;
  tokens_out: number | null;
}

export interface FindNullsResponse {
  drawing_id: string;
  locations: { path: string; bbox_xyxy: number[] | null;
               value_hint: string | null; confidence: string }[];
  image_size: number[];
  latency_s: number;
  tokens_in: number | null;
  tokens_out: number | null;
}

export interface GapItem {
  field: string;
  reason: string;
  severity: "high" | "medium" | "low";
  bbox_xyxy: number[] | null;
  suggestion: string | null;
}

export interface GapReport {
  drawing_id: string | null;
  image_path: string | null;
  image_width: number;
  image_height: number;
  gaps: GapItem[];
  total_score: number;
}

export interface EstimateLine {
  gesn_code: string;
  description: string;
  unit: string;
  quantity: number;
  unit_price: number | null;
  total: number | null;
  source_fields: string[];
}

export interface EstimateResult {
  drawing_id: string | null;
  lines: EstimateLine[];
  total: number;
  currency: string;
}

export interface Drawing {
  drawing_id: string;
  dataset: string;
  filename: string;
  image_path: string;
  image_url: string;
}

// Materials (colour pass) + reconciliation.
export interface MaterialItem {
  name: string;
  position: string | null;
  unit: string | null;
  quantity: number | null;
  color_hex: string | null;
  source: "table" | "color" | "both";
  note: string | null;
}

export interface MaterialsResult {
  drawing_id: string | null;
  materials: MaterialItem[];
  tables_found: number;
  legend_found: boolean;
  raw: string;
  latency_s: number;
  tokens_in: number | null;
  tokens_out: number | null;
}

export interface ReconcileItem {
  name: string;
  status: "matched" | "only_in_assembly" | "only_in_spec";
  in_assembly: boolean;
  in_spec: boolean;
  matched_to: string | null;
  spec_unit: string | null;
  spec_quantity: number | null;
  assembly_refs: string[];
}

export interface ReconcileReport {
  drawing_id: string | null;
  items: ReconcileItem[];
  matched: number;
  only_in_assembly: number;
  only_in_spec: number;
}

export interface MaterialsResponse {
  drawing_id: string;
  materials: MaterialsResult;
  reconcile: ReconcileReport | null;
}

export interface PassMeta {
  model: string;
  pass: number;
  latency_s: number;
  prompt_tokens: number | null;
  output_tokens: number | null;
}

export interface ExtractResponse {
  drawing_id: string;
  pass1: Pass1Facts;
  pass2: DrawingJSON;
  pass1_meta: PassMeta;
  pass2_meta: PassMeta;
  image_path: string;
  image_url: string;
}

export interface CropReaskResponse {
  field: string;
  value: unknown;
  raw: string;
  confidence: string;
}

export interface CycleStep {
  name: string;
  ok: boolean;
  duration_s: number;
  note: string;
  detail: Record<string, unknown>;
}

export interface FullCycleResponse {
  drawing_id: string;
  image_path: string;
  steps: CycleStep[];
  total_latency_s: number;
  total_tokens: {
    input: number;
    output: number;
    total: number;
    cache_hits?: number;
    hash?: string;
    key?: string;
    hash_compute_s?: number;
  };
  pass1: Pass1Facts;
  pass2: DrawingJSON;
  pass3: { pass3: DrawingJSON; applied: { path: string; value: unknown;
             bbox_xyxy: number[] | null; source_pass1_idx: number }[];
             conflicts: unknown[]; new_nulls: string[] } | null;
  auto_detect: { field: string; severity: string;
                  predicted_bbox_xyxy: number[] | null; method: string }[];
  gap_report: GapReport | null;
  final_gaps: GapReport | null;
  estimate: EstimateResult | null;
  xlsx_path: string | null;
  error: string | null;
}
