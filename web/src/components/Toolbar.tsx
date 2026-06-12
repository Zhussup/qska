import React from "react";
import { Cog, Search, Calculator, Rocket } from "lucide-react";
import type { Drawing, EstimateResult, FullCycleResponse } from "../types";
import { api } from "../api";

interface Props {
  drawing: Drawing | null;
  busy: boolean;
  hasPass2: boolean;
  onExtract: () => void;
  onGaps: () => void;
  onEstimate: (r: EstimateResult) => void;
  onStatus: (msg: string, kind: "ok" | "err" | "info") => void;
  onFullCycle: (r: FullCycleResponse) => void;
}

const ICON = { size: 14, strokeWidth: 1.75 } as const;

export function Toolbar({ drawing, busy, hasPass2, onExtract, onGaps, onEstimate, onStatus, onFullCycle }: Props) {
  const [health, setHealth] = React.useState<string>("");
  const [force, setForce] = React.useState(false);
  React.useEffect(() => { api.health().then(h => setHealth(`${h.extraction_model} / ${h.crop_model}`)).catch(() => {}); }, []);

  async function runEstimate() {
    if (!drawing) return;
    try {
      onStatus("estimate…", "info");
      const r = await api.estimate(drawing.drawing_id);
      onEstimate(r);
      onStatus(`estimate ok: ${r.lines.length} lines, total=${r.total} ${r.currency}`, "ok");
    } catch (e) {
      onStatus((e as Error).message, "err");
    }
  }

  async function runFullCycle() {
    if (!drawing) return;
    try {
      onStatus(force ? "full cycle (FORCE re-extract)…" : "full cycle (cache ok)…", "info");
      const r = await api.fullCycle(drawing.image_path, drawing.drawing_id, force);
      onFullCycle(r);
      const stepSummary = r.steps.map(s => `${s.name} ${s.ok ? "✓" : "✗"} ${s.duration_s}s`).join(" → ");
      const cache = r.total_tokens?.cache_hits ?? 0;
      const err = r.error ? `, error: ${r.error}` : "";
      onStatus(`full cycle: ${r.total_latency_s}s, ${cache} cache hits, ${stepSummary}${err}`,
               r.error ? "err" : "ok");
    } catch (e) {
      onStatus((e as Error).message, "err");
    }
  }

  return (
    <div className="topbar">
      <h1>qsmeta</h1>
      <span className="badge">{health || "loading…"}</span>
      <div className="spacer" />
      <label title="Игнорировать кэш, пересчитать pass1+pass2 с нуля">
        <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)} />
        force
      </label>
      <div className="divider" />
      <button onClick={onExtract} disabled={!drawing || busy}>
        <Cog {...ICON} /> Extract
      </button>
      <button onClick={onGaps} disabled={!drawing || busy}>
        <Search {...ICON} /> Gaps
      </button>
      <button
        onClick={runEstimate}
        disabled={!drawing || !hasPass2 || busy}
        title={hasPass2 ? "Посчитать смету по извлечённым данным" : "Сначала запусти Extract"}
      >
        <Calculator {...ICON} /> Estimate
      </button>
      <button
        className="primary"
        onClick={runFullCycle}
        disabled={!drawing || busy}
        title={force ? "FORCE: re-extract pass1+pass2, ignore cache" : "Use cache when possible"}
      >
        <Rocket {...ICON} /> Full cycle
      </button>
    </div>
  );
}
