import React, { useEffect, useRef, useState, useCallback } from "react";
import {
  Sparkles, ImageIcon, Layers, FileSpreadsheet, Calculator, Wand2,
  ListChecks, AlertTriangle, Palette,
} from "lucide-react";
import { api, loadImageObjectUrl } from "./api";
import type {
  Drawing, DrawingJSON, EstimateResult, FullCycleResponse,
  GapItem, GapReport, Pass1Facts, ExtractResponse,
} from "./types";
import { Sidebar } from "./components/Sidebar";
import { DrawingCanvas } from "./components/DrawingCanvas";
import { EstimateTable } from "./components/EstimateTable";
import { Toolbar } from "./components/Toolbar";
import { LayerPanel, DEFAULT_LAYERS, LayerState } from "./components/LayerPanel";
import { GapPanel } from "./components/GapPanel";
import { FactsPanel } from "./components/FactsPanel";
import { MaterialsPanel } from "./components/MaterialsPanel";

const ICON_TINY = { size: 12, strokeWidth: 1.75 } as const;
const ICON_MED  = { size: 16, strokeWidth: 1.75 } as const;

type RightTab = "layers" | "facts" | "gaps" | "materials" | "estimate";
const SIDEBAR_MIN = 180;
const SIDEBAR_MAX = 560;

export default function App() {
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const [selected, setSelected] = useState<Drawing | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ msg: string; kind: "ok" | "err" | "info" }>({ msg: "ready", kind: "info" });

  const [pass1, setPass1] = useState<Pass1Facts | null>(null);
  const [pass2, setPass2] = useState<DrawingJSON | null>(null);
  const [gaps, setGaps] = useState<GapReport | null>(null);
  const [estimate, setEstimate] = useState<EstimateResult | null>(null);
  const [cycle, setCycle] = useState<FullCycleResponse | null>(null);
  const [selectedGap, setSelectedGap] = useState<GapItem | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ w: number; h: number } | null>(null);
  const [displaySize, setDisplaySize] = useState<{ w: number; h: number; scale: number }>({ w: 0, h: 0, scale: 1 });
  const [layers, setLayers] = useState<LayerState>(DEFAULT_LAYERS);
  const [rightTab, setRightTab] = useState<RightTab>("layers");
  const [sidebarW, setSidebarW] = useState(260);
  const resizing = useRef(false);

  useEffect(() => {
    api.drawings().then(setDrawings).catch(e => setStatus({ msg: e.message, kind: "err" }));
  }, []);

  const onSelect = useCallback((d: Drawing) => {
    setSelected(d);
    setPass1(null); setPass2(null); setGaps(null); setEstimate(null);
    setCycle(null);
    setSelectedGap(null);
    setNaturalSize(null);
  }, []);

  useEffect(() => {
    if (!selected || naturalSize) return;
    const img = new Image();
    img.onload = () => {
      const maxW = 1400;
      const maxH = Math.min(window.innerHeight - 120, 1600);
      const scale = Math.min(maxW / img.naturalWidth, img.naturalHeight / maxH, 1);
      setNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
      setDisplaySize({
        w: Math.round(img.naturalWidth * scale),
        h: Math.round(img.naturalHeight * scale),
        scale,
      });
    };
    loadImageObjectUrl(selected.image_url).then((u) => { img.src = u; });
  }, [selected, naturalSize]);

  // Sidebar resize: drag the splitter, clamp width, update on window move.
  useEffect(() => {
    function onMove(ev: MouseEvent) {
      if (!resizing.current) return;
      ev.preventDefault();
      setSidebarW(Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, ev.clientX)));
    }
    function onUp() {
      if (!resizing.current) return;
      resizing.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startResize = useCallback(() => {
    resizing.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  async function onReaskGap(g: GapItem) {
    if (!selected || !g.bbox_xyxy) return;
    setBusy(true);
    setStatus({ msg: `переспрашиваю ${g.field}…`, kind: "info" });
    try {
      const r = await api.cropReask(selected.drawing_id, g.field, g.bbox_xyxy, g.suggestion ?? undefined);
      setStatus({ msg: `${g.field} → ${JSON.stringify(r.value)} (${r.confidence})`, kind: "ok" });
    } catch (e) {
      setStatus({ msg: (e as Error).message, kind: "err" });
    } finally {
      setBusy(false);
    }
  }

  async function runExtract() {
    if (!selected) return;
    setBusy(true);
    setStatus({ msg: "two-pass extraction…", kind: "info" });
    try {
      const r: ExtractResponse = await api.extract(selected.image_path, selected.drawing_id);
      setPass1(r.pass1);
      setPass2(r.pass2);
      setStatus({
        msg: `extracted in ${r.pass1_meta.latency_s + r.pass2_meta.latency_s}s ` +
             `(p1: ${r.pass1_meta.output_tokens ?? "?"} tok / p2: ${r.pass2_meta.output_tokens ?? "?"} tok)`,
        kind: "ok",
      });
    } catch (e) {
      setStatus({ msg: (e as Error).message, kind: "err" });
    } finally {
      setBusy(false);
    }
  }

  function onApplyCrop(field: string, value: unknown) {
    if (!pass2) return;
    if (field in pass2) {
      setPass2({ ...pass2, [field]: value as any });
      setStatus({ msg: `applied ${field} = ${JSON.stringify(value)}`, kind: "ok" });
    } else {
      setStatus({ msg: `field ${field} not a top-level key — manual edit needed`, kind: "err" });
    }
  }

  async function onManualBbox(bbox: [number, number, number, number]) {
    if (!selected) return;
    const field = window.prompt("Field path to fill", "thickness_mm");
    if (!field) return;
    const hint = window.prompt("Hint for the model", "") ?? undefined;
    setBusy(true);
    setStatus({ msg: `manual re-ask on ${field}…`, kind: "info" });
    try {
      const r = await api.cropReask(selected.drawing_id, field, bbox, hint);
      onApplyCrop(r.field, r.value);
      setStatus({ msg: `manual re-ask ok: ${field} = ${JSON.stringify(r.value)}`, kind: "ok" });
    } catch (e) {
      setStatus({ msg: (e as Error).message, kind: "err" });
    } finally {
      setBusy(false);
    }
  }

  async function onLetAiDoIt(bbox: [number, number, number, number]) {
    if (!selected || !pass2) return;
    setBusy(true);
    setStatus({ msg: "letting AI fill visible nulls in your selection…", kind: "info" });
    try {
      const r = await api.augment(selected.image_path, selected.drawing_id, bbox);
      if (r.applied && r.applied.length) {
        setStatus({
          msg: `augment: applied ${r.applied.length} field(s) in ${r.latency_s}s — ` +
               r.applied.map(p => `${p}=${JSON.stringify(r.patches.find(x => x.path === p)?.value)}`).join(", "),
          kind: "ok",
        });
        const refreshed = await api.pass3(selected.drawing_id);
        setPass2(refreshed.drawing as DrawingJSON);
        // Re-derive gaps from the now-richer Pass2.
        if (refreshed.merge && refreshed.merge.new_nulls) {
          // We can synthesise a small GapReport inline.
          setGaps({
            drawing_id: selected.drawing_id,
            image_path: selected.image_path,
            image_width: naturalSize?.w ?? 0,
            image_height: naturalSize?.h ?? 0,
            gaps: refreshed.merge.new_nulls.map((p, i) => ({
              field: p,
              reason: "null",
              severity: "medium" as const,
              bbox_xyxy: null,
              suggestion: null,
            })),
            total_score: 0.5,
          });
        }
      } else {
        setStatus({ msg: "augment: no fillable fields found in selection", kind: "info" });
      }
    } catch (e) {
      setStatus({ msg: (e as Error).message, kind: "err" });
    } finally {
      setBusy(false);
    }
  }

  async function runGaps() {
    if (!selected) return;
    setBusy(true);
    setStatus({ msg: "gap detection…", kind: "info" });
    try {
      const g = await api.gaps(selected.drawing_id);
      setGaps(g);
      setStatus({ msg: `score=${g.total_score}, ${g.gaps.length} gaps`, kind: "ok" });
    } catch (e) {
      setStatus({ msg: (e as Error).message, kind: "err" });
    } finally {
      setBusy(false);
    }
  }

  // Auto-recompute gaps after a new pass2 lands so the canvas always reflects them.
  useEffect(() => {
    if (pass2 && selected) {
      runGaps();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pass2?.title_block?.project_code]);

  const filteredGaps = (gaps?.gaps ?? []).filter(g => {
    if (g.severity === "high") return layers.gapsHigh;
    if (g.severity === "medium") return layers.gapsMedium;
    return layers.gapsLow;
  });

  const nullCount = gaps?.gaps.length ?? 0;
  const elevCount = pass1?.elevations.length ?? 0;
  const dimCount = pass1?.dimensions.length ?? 0;
  const nodeCount = pass1?.node_markers.length ?? 0;

  return (
    <div className="app" style={{ ["--sidebar-w" as any]: `${sidebarW}px` }}>
      <Toolbar
        drawing={selected}
        busy={busy}
        hasPass2={pass2 !== null}
        onExtract={runExtract}
        onGaps={runGaps}
        onEstimate={(r) => setEstimate(r)}
        onStatus={(msg, kind) => setStatus({ msg, kind })}
        onFullCycle={(r) => {
          setCycle(r);
          if (r.pass1) setPass1(r.pass1);
          if (r.pass2) setPass2(r.pass2);
          if (r.gap_report) setGaps(r.gap_report);
          if (r.estimate) setEstimate(r.estimate);
          setStatus({
            msg: `full cycle: ${r.total_latency_s}s, ${r.total_tokens?.cache_hits ?? 0} cache, ` +
                 `${r.pass3?.applied.length ?? 0} merged fields`,
            kind: "ok",
          });
        }}
      />

      <Sidebar
        drawings={drawings}
        selected={selected?.drawing_id ?? null}
        onSelect={onSelect}
        onRefresh={() => api.drawings().then(setDrawings).catch(e => setStatus({ msg: e.message, kind: "err" }))}
      />

      <div
        className="col-resizer"
        style={{ left: sidebarW }}
        onMouseDown={startResize}
        title="Потяни, чтобы изменить ширину панели"
      />

      {selected && naturalSize && displaySize.w > 0 ? (
        <DrawingCanvas
          imageUrl={selected.image_url}
          naturalWidth={naturalSize.w}
          naturalHeight={naturalSize.h}
          displayWidth={displaySize.w}
          displayHeight={displaySize.h}
          gaps={filteredGaps}
          pass1Facts={pass1}
          layers={layers}
          selectedGap={selectedGap}
          onSelectGap={setSelectedGap}
          onSelectBbox={onManualBbox}
          onLetAiDoIt={pass2 ? onLetAiDoIt : undefined}
        />
      ) : (
        <div className="canvas-area">
          <div className="empty">
            {selected
              ? <><ImageIcon size={36} strokeWidth={1.2} /><div>Загрузка изображения…</div></>
              : <><ImageIcon size={36} strokeWidth={1.2} /><div>Выберите чертёж слева</div></>}
          </div>
        </div>
      )}

      <aside className="right-pane">
        <div className="right-tabs">
          <button className={"right-tab" + (rightTab === "layers" ? " active" : "")} onClick={() => setRightTab("layers")} title="Слои визуализации">
            <Layers {...ICON_TINY} /> Слои
          </button>
          <button className={"right-tab" + (rightTab === "facts" ? " active" : "")} onClick={() => setRightTab("facts")} title="Извлечённые метрики">
            <ListChecks {...ICON_TINY} /> Факты
            {(elevCount + dimCount + nodeCount) > 0 && <span className="right-tab-n">{elevCount + dimCount + nodeCount}</span>}
          </button>
          <button className={"right-tab" + (rightTab === "gaps" ? " active" : "")} onClick={() => setRightTab("gaps")} title="Пропуски">
            <AlertTriangle {...ICON_TINY} /> Пропуски
            {nullCount > 0 && <span className="right-tab-n">{nullCount}</span>}
          </button>
          <button className={"right-tab" + (rightTab === "materials" ? " active" : "")} onClick={() => setRightTab("materials")} title="Материалы (цветной пасс) + сверка">
            <Palette {...ICON_TINY} /> Материалы
          </button>
          <button className={"right-tab" + (rightTab === "estimate" ? " active" : "")} onClick={() => setRightTab("estimate")} title="Смета">
            <Calculator {...ICON_TINY} /> Смета
          </button>
        </div>

        <div className="right-pane-body">
          {rightTab === "layers" && (
            <>
              <LayerPanel
                layers={layers}
                onChange={setLayers}
                pass1Facts={pass1}
                gaps={gaps?.gaps ?? []}
              />
              {cycle?.pass3 && (
                <div className="pass3-mini">
                  <div className="pass3-mini-title">
                    <Wand2 {...ICON_TINY} /> Pass3 merge
                  </div>
                  <div className="pass3-mini-stat">
                    заполнил <b>{cycle.pass3.applied.length}</b> из Pass1
                  </div>
                  {cycle.pass3.conflicts.length > 0 && (
                    <div className="pass3-mini-stat warn">
                      {cycle.pass3.conflicts.length} конфликт(ов)
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {rightTab === "facts" && (
            <div className="right-pane-content"><FactsPanel facts={pass1} /></div>
          )}

          {rightTab === "gaps" && (
            <div className="right-pane-content">
              <GapPanel
                gaps={gaps?.gaps ?? []}
                selectedGap={selectedGap}
                onSelectGap={setSelectedGap}
                onReask={onReaskGap}
                busy={busy}
              />
            </div>
          )}

          {rightTab === "materials" && (
            <div className="right-pane-content">
              <MaterialsPanel
                drawing={selected}
                hasPass2={pass2 !== null}
                onStatus={(msg, kind) => setStatus({ msg, kind })}
              />
            </div>
          )}

          {rightTab === "estimate" && (
            <div className="right-pane-content">
              <EstimateTable
                drawingId={selected?.drawing_id ?? ""}
                result={estimate}
                onResult={setEstimate}
                hasPass2={pass2 !== null}
              />
            </div>
          )}
        </div>
      </aside>

      <div className="statusbar">
        <span className={status.kind === "ok" ? "ok" : status.kind === "err" ? "err" : status.kind === "info" && busy ? "busy" : ""}>
          {busy && <><span className="spinner" /> </>}
          {status.msg}
        </span>
        {pass2 && <span>score: {(gaps?.total_score ?? 0).toFixed(2)}</span>}
        {selected && (
          <span title={selected.image_path} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 380 }}>
            {selected.dataset} / {selected.filename}
          </span>
        )}
        <div className="spacer" />
        <span className="version">v0.2.0</span>
      </div>
    </div>
  );
}
