// Thin fetch wrappers around /api/*. All paths are relative — Vite dev proxy
// routes them to FastAPI on :8000.

import type {
  Drawing,
  ExtractResponse,
  GapReport,
  Pass1Facts,
  CropReaskResponse,
  AugmentResponse,
  FindNullsResponse,
  EstimateResult,
  DrawingJSON,
  FullCycleResponse,
  MaterialsResponse,
} from "./types";

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) {
    const txt = await r.text().catch(() => r.statusText);
    throw new Error(`GET ${path} failed: ${r.status} ${txt}`);
  }
  return r.json() as Promise<T>;
}

async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const txt = await r.text().catch(() => r.statusText);
    throw new Error(`POST ${path} failed: ${r.status} ${txt}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => jget<{ ok: boolean; extraction_model: string; crop_model: string }>("/api/health"),
  drawings: () => jget<Drawing[]>("/api/drawings"),
  uploadPdf: async (file: File): Promise<{ dataset: string; pages: number; drawings: Drawing[] }> => {
    const form = new FormData();
    form.append("file", file);
    const r = await fetch("/api/upload_pdf", { method: "POST", body: form });
    if (!r.ok) {
      const txt = await r.text().catch(() => r.statusText);
      throw new Error(`POST /api/upload_pdf failed: ${r.status} ${txt}`);
    }
    return r.json();
  },
  extract: (image_path: string, drawing_id?: string) =>
    jpost<ExtractResponse>("/api/extract", { image_path, drawing_id }),
  gaps: (drawing_id: string) => jpost<GapReport>("/api/gaps", { drawing_id }),
  cropReask: (drawing_id: string, field: string, bbox_xyxy: number[], hint?: string) =>
    jpost<CropReaskResponse>("/api/crop_reask", { drawing_id, field, bbox_xyxy, hint }),
  estimate: (drawing_id: string) =>
    jpost<EstimateResult>("/api/estimate", { drawing_id }),
  estimateExport: (drawing_id: string) =>
    jpost<{ path: string; total: number; currency: string }>(
      "/api/estimate_export",
      { drawing_id }
    ),
  fullCycle: (image_path: string, drawing_id?: string, force = false) =>
    jpost<FullCycleResponse>("/api/full_cycle", { image_path, drawing_id, force }),
  augment: (image_path: string, drawing_id: string, bbox_xyxy: number[], hint = "") =>
    jpost<{ patches: { path: string; value: unknown }[]; applied: string[]; skipped: unknown[];
             bbox: number[]; raw: string; latency_s: number }>(
      "/api/augment", { image_path, drawing_id, bbox_xyxy, hint }),
  findNulls: (image_path: string, drawing_id: string) =>
    jpost<{ locations: { path: string; bbox_xyxy: number[] | null;
                          value_hint: string | null; confidence: string }[];
             image_size: number[]; latency_s: number }>(
      "/api/find_nulls", { image_path, drawing_id }),
  materials: (drawing_id: string, image_path?: string, force = false) =>
    jpost<MaterialsResponse>("/api/materials", { drawing_id, image_path, force }),
  pass3: (drawing_id: string) =>
    jpost<{ drawing: DrawingJSON;
            pass1_facts: { elevations: unknown[]; dimensions: unknown[]; node_markers: unknown[] } | null;
            merge: { applied: unknown[]; conflicts: unknown[]; new_nulls: string[] } | null }>(
      "/api/pass3", { drawing_id }),
};

// The image payload is heavy; we load it once and cache as object URL.
const imageCache = new Map<string, string>();
export async function loadImageObjectUrl(url: string): Promise<string> {
  if (imageCache.has(url)) return imageCache.get(url)!;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`image fetch ${url} failed: ${r.status}`);
  const data = (await r.json()) as { mime: string; data: string };
  const blob = await (await fetch(`data:${data.mime};base64,${data.data}`)).blob();
  const objUrl = URL.createObjectURL(blob);
  imageCache.set(url, objUrl);
  return objUrl;
}
