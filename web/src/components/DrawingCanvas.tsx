import React, { useEffect, useRef, useState } from "react";
import { Sparkles, X, ZoomIn, ZoomOut, Maximize2 } from "lucide-react";
import { loadImageObjectUrl } from "../api";
import type { GapItem, Pass1Facts } from "../types";
import type { LayerState } from "./LayerPanel";

interface Props {
  imageUrl: string;
  naturalWidth: number;
  naturalHeight: number;
  displayWidth: number;
  displayHeight: number;
  gaps: GapItem[];
  pass1Facts?: Pass1Facts | null;
  layers: LayerState;
  selectedGap: GapItem | null;
  onSelectGap: (g: GapItem | null) => void;
  /** Called when the user releases the mouse after drawing a bbox. */
  onSelectBbox?: (xyxy: [number, number, number, number]) => void;
  /** Called when the user confirms the current draw with "Let AI do it". */
  onLetAiDoIt?: (xyxy: [number, number, number, number]) => void;
}

const DRAW_ICON = { size: 13, strokeWidth: 2 } as const;
const BTN_ICON = { size: 14, strokeWidth: 1.75 } as const;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;

type Mode = "draw" | "pan";

export function DrawingCanvas({
  imageUrl,
  naturalWidth,
  naturalHeight,
  displayWidth,
  displayHeight,
  gaps,
  pass1Facts,
  layers,
  selectedGap,
  onSelectGap,
  onSelectBbox,
  onLetAiDoIt,
}: Props) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [mode, setMode] = useState<Mode>("draw");
  const wrapRef = useRef<HTMLDivElement>(null);
  const [committed, setCommitted] = useState<[number, number, number, number] | null>(null);
  const drawRef = useRef<{ x1: number; y1: number } | null>(null);
  const panRef = useRef<{ x: number; y: number } | null>(null);
  const [draw, setDraw] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);

  useEffect(() => {
    let active = true;
    loadImageObjectUrl(imageUrl).then((u) => { if (active) setObjectUrl(u); });
    return () => { active = false; };
  }, [imageUrl]);

  useEffect(() => {
    setCommitted(null); setZoom(1); setPan({ x: 0, y: 0 });
  }, [imageUrl, naturalWidth, naturalHeight]);

  // Wheel zoom — must be non-passive so preventDefault() actually works.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const handler = (ev: WheelEvent) => {
      ev.preventDefault();
      const factor = ev.deltaY < 0 ? 1.15 : 1 / 1.15;
      setZoom(z => {
        const nz = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, z * factor));
        const rect = el.getBoundingClientRect();
        const sx = ev.clientX - rect.left - pan.x;
        const sy = ev.clientY - rect.top  - pan.y;
        const fit = Math.min(displayWidth / naturalWidth, displayHeight / naturalHeight, 1);
        const oldImgX = sx / (z * fit);
        const oldImgY = sy / (z * fit);
        setPan(() => ({
          x: sx - oldImgX * nz * fit,
          y: sy - oldImgY * nz * fit,
        }));
        return nz;
      });
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [pan, displayWidth, displayHeight, naturalWidth, naturalHeight]);

  // Coordinates of a viewport-space (CSS pixel) point on the *image*.
  // This handles zoom + pan: we invert the transform.
  function screenToImage(clientX: number, clientY: number): [number, number] {
    const rect = wrapRef.current!.getBoundingClientRect();
    // Position in CSS pixels relative to the wrap element, including zoom/pan.
    const sx = clientX - rect.left - pan.x;
    const sy = clientY - rect.top  - pan.y;
    // Map back to natural-image pixels: undo zoom, undo "fit to viewport" scale.
    const fit = Math.min(displayWidth / naturalWidth, displayHeight / naturalHeight, 1);
    return [
      Math.round(sx / (zoom * fit)),
      Math.round(sy / (zoom * fit)),
    ];
  }

  function onMouseDown(ev: React.MouseEvent) {
    if (ev.button === 1 || (ev.button === 0 && mode === "pan") || ev.altKey) {
      // Middle button or Alt-drag → pan.
      ev.preventDefault();
      panRef.current = { x: ev.clientX, y: ev.clientY };
      wrapRef.current!.style.cursor = "grabbing";
      return;
    }
    if (ev.button !== 0) return;
    const [x, y] = screenToImage(ev.clientX, ev.clientY);
    drawRef.current = { x1: x, y1: y };
    setDraw({ x1: x, y1: y, x2: x, y2: y });
    setCommitted(null);
  }
  function onMouseMove(ev: React.MouseEvent) {
    if (panRef.current) {
      setPan(p => ({
        x: p.x + (ev.clientX - panRef.current!.x),
        y: p.y + (ev.clientY - panRef.current!.y),
      }));
      panRef.current = { x: ev.clientX, y: ev.clientY };
      return;
    }
    if (!drawRef.current) return;
    const [x, y] = screenToImage(ev.clientX, ev.clientY);
    setDraw({ ...drawRef.current, x2: x, y2: y });
  }
  function onMouseUp() {
    if (panRef.current) {
      panRef.current = null;
      wrapRef.current!.style.cursor = mode === "pan" ? "grab" : (interactive ? "crosshair" : "default");
      return;
    }
    if (!drawRef.current) return;
    const { x1, y1 } = drawRef.current;
    const d = draw!;
    drawRef.current = null;
    setDraw(null);
    const bbox: [number, number, number, number] = [
      Math.min(x1, d.x2), Math.min(y1, d.y2),
      Math.max(x1, d.x2), Math.max(y1, d.y2),
    ];
    if (bbox[2] - bbox[0] <= 8 || bbox[3] - bbox[1] <= 8) return;
    onSelectBbox?.(bbox);
    setCommitted(bbox);
  }
  function onMouseLeave() {
    panRef.current = null;
  }


  function resetView() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  const p1Elev   = layers.pass1Elevations ? (pass1Facts?.elevations   ?? []).filter(e => e.bbox_xyxy) : [];
  const p1Dim    = layers.pass1Dimensions ? (pass1Facts?.dimensions   ?? []).filter(d => d.bbox_xyxy) : [];
  const p1Nodes  = layers.pass1Nodes      ? (pass1Facts?.node_markers ?? []).filter(n => n.bbox_xyxy) : [];

  const fit = Math.min(displayWidth / naturalWidth, displayHeight / naturalHeight, 1);
  const contentTransform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom * fit})`;
  const interactive = !!(onSelectBbox || onLetAiDoIt);

  return (
    <div className="canvas-area">
      {!objectUrl && <div className="empty">Загрузка изображения…</div>}
      {objectUrl && (
        <>
          {/* Floating zoom controls. */}
          <div className="canvas-controls">
            <button
              className="ghost"
              title={mode === "draw" ? "Рисование активно. Click — выделить зону. Hold Alt — pan." : "Pan активен. Drag — двигать."}
              onClick={() => setMode(m => m === "draw" ? "pan" : "draw")}
            >
              {mode === "draw" ? "✏ draw" : "✋ pan"}
            </button>
            <button className="ghost" onClick={() => setZoom(z => Math.max(MIN_ZOOM, z / 1.25))} title="Zoom out (-)"><ZoomOut {...BTN_ICON} /></button>
            <span className="canvas-zoom-label">{Math.round(zoom * 100)}%</span>
            <button className="ghost" onClick={() => setZoom(z => Math.min(MAX_ZOOM, z * 1.25))} title="Zoom in (+)"><ZoomIn {...BTN_ICON} /></button>
            <button className="ghost" onClick={resetView} title="Reset view (0)"><Maximize2 {...BTN_ICON} /></button>
          </div>

          <div
            ref={wrapRef}
            className="canvas-wrap"
            style={{
              width: displayWidth,
              height: displayHeight,
              cursor: mode === "pan" ? "grab" : (interactive ? "crosshair" : "default"),
              overflow: "hidden",
              position: "relative",
            }}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            onMouseLeave={onMouseLeave}
          >
            <div
              className="canvas-content"
              style={{
                position: "absolute",
                top: 0, left: 0,
                width: naturalWidth,
                height: naturalHeight,
                transformOrigin: "0 0",
                transform: contentTransform,
              }}
            >
              <img src={objectUrl} width={naturalWidth} height={naturalHeight} alt="drawing"
                   draggable={false} style={{ display: "block" }} />

              {/* Pass1 elevation bboxes + value label */}
              {p1Elev.map((e, i) => {
                const [x1, y1, x2, y2] = e.bbox_xyxy!;
                return (
                  <div key={`p1e${i}`} className="bbox-overlay p1-elev" style={{
                    left: x1, top: y1, width: x2 - x1, height: y2 - y1,
                  }} title={`elev: ${e.value} → ${e.points_to}`}>
                    <span className="bbox-label bbox-label-elev">{e.value}</span>
                  </div>
                );
              })}
              {p1Dim.map((d, i) => {
                const [x1, y1, x2, y2] = d.bbox_xyxy!;
                return (
                  <div key={`p1d${i}`} className="bbox-overlay p1-dim" style={{
                    left: x1, top: y1, width: x2 - x1, height: y2 - y1,
                  }} title={`dim: ${d.value_mm}мм (${d.between})`}>
                    <span className="bbox-label bbox-label-dim">{d.value_mm}мм</span>
                  </div>
                );
              })}
              {p1Nodes.map((n, i) => {
                const [x1, y1, x2, y2] = n.bbox_xyxy!;
                return (
                  <div key={`p1n${i}`} className="bbox-overlay p1-node" style={{
                    left: x1, top: y1, width: x2 - x1, height: y2 - y1,
                  }} title={`node: ${n.marker}${n.labels ? " — " + n.labels : ""}`}>
                    <span className="bbox-label bbox-label-node">{n.marker}</span>
                  </div>
                );
              })}

              {/* Gap bboxes (interactive) */}
              {gaps.map((g, i) => {
                if (!g.bbox_xyxy) return null;
                const [x1, y1, x2, y2] = g.bbox_xyxy;
                const left = x1, top = y1;
                const w = x2 - x1, h = y2 - y1;
                const selected = selectedGap === g;
                return (
                  <div key={`g${i}`}
                       className={`bbox ${g.severity}` + (selected ? " selected" : "")}
                       style={{ left, top, width: w, height: h,
                                outline: selected ? "2px solid var(--selected-stroke)" : undefined,
                                background: selected ? "var(--selected-fill)" : undefined }}
                       onClick={(ev) => { ev.stopPropagation(); onSelectGap(g); }}
                       title={`${g.field} — ${g.suggestion ?? ""}`}
                  />
                );
              })}

              {draw && (
                <div className="bbox" style={{
                  left: Math.min(draw.x1, draw.x2),
                  top: Math.min(draw.y1, draw.y2),
                  width: Math.abs(draw.x2 - draw.x1),
                  height: Math.abs(draw.y2 - draw.y1),
                  borderColor: "var(--selected-stroke)",
                  background: "var(--selected-fill)",
                }} />
              )}

              {committed && onLetAiDoIt && (
                <>
                  <div className="bbox" style={{
                    left: committed[0], top: committed[1],
                    width: committed[2] - committed[0],
                    height: committed[3] - committed[1],
                    borderColor: "var(--selected-stroke)",
                    background: "var(--selected-fill)",
                    borderStyle: "solid", borderWidth: 2,
                  }} />
                  <div className="bbox-ai-fab" style={{
                    position: "absolute",
                    left: (committed[2]) + 8,
                    top: (committed[1]) + 8,
                  }}>
                    <button className="primary" onClick={() => onLetAiDoIt(committed)}
                            title="Модель посмотрит на выделение и заполнит все видимые null-поля">
                      <Sparkles {...DRAW_ICON} /> Let AI do it
                    </button>
                    <button className="ghost" onClick={() => setCommitted(null)} title="Сбросить выделение">
                      <X {...DRAW_ICON} />
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
