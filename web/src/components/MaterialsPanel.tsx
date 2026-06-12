import React, { useState } from "react";
import { Palette, RefreshCw, CheckCircle2, AlertCircle, FileQuestion } from "lucide-react";
import { api } from "../api";
import type { Drawing, MaterialsResult, ReconcileReport, ReconcileItem } from "../types";

interface Props {
  drawing: Drawing | null;
  hasPass2: boolean;
  onStatus: (msg: string, kind: "ok" | "err" | "info") => void;
}

const ICON = { size: 13, strokeWidth: 1.75 } as const;
type View = "spec" | "reconcile";

const STATUS_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  matched:           { label: "совпадает",       color: "var(--ok)",     icon: <CheckCircle2 {...ICON} /> },
  only_in_assembly:  { label: "нет в спец-ии",    color: "var(--warn)",   icon: <AlertCircle {...ICON} /> },
  only_in_spec:      { label: "не в сборках",     color: "var(--danger)", icon: <FileQuestion {...ICON} /> },
};

export function MaterialsPanel({ drawing, hasPass2, onStatus }: Props) {
  const [busy, setBusy] = useState(false);
  const [mats, setMats] = useState<MaterialsResult | null>(null);
  const [rec, setRec] = useState<ReconcileReport | null>(null);
  const [view, setView] = useState<View>("reconcile");

  async function run(force = false) {
    if (!drawing) return;
    setBusy(true);
    onStatus("цветной пасс материалов…", "info");
    try {
      const r = await api.materials(drawing.drawing_id, drawing.image_path, force);
      setMats(r.materials);
      setRec(r.reconcile);
      setView(r.reconcile ? "reconcile" : "spec");
      onStatus(
        `материалы: ${r.materials.materials.length} поз. ` +
        `(таблиц: ${r.materials.tables_found}, легенда: ${r.materials.legend_found ? "да" : "нет"})` +
        (r.reconcile ? ` · сверка: ${r.reconcile.matched}✓ / ${r.reconcile.only_in_assembly}⚠ / ${r.reconcile.only_in_spec}✗` : ""),
        "ok",
      );
    } catch (e) {
      onStatus((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  if (!mats) {
    return (
      <div className="materials-panel">
        <button className="primary" onClick={() => run(false)} disabled={!drawing || busy}>
          {busy ? <><span className="spinner" /> читаю…</> : <><Palette {...ICON} /> Извлечь материалы</>}
        </button>
        <div className="materials-hint">
          Цветной пасс читает таблицы спецификации и легенду штриховки (без grayscale).
          {!hasPass2 && " Сверка появится после Extract."}
        </div>
      </div>
    );
  }

  return (
    <div className="materials-panel">
      <div className="materials-toolbar">
        <div className="materials-viewtabs">
          {rec && (
            <button className={"facts-tab" + (view === "reconcile" ? " active" : "")} onClick={() => setView("reconcile")}>
              Сверка <span className="facts-tab-n">{rec.items.length}</span>
            </button>
          )}
          <button className={"facts-tab" + (view === "spec" ? " active" : "")} onClick={() => setView("spec")}>
            Спецификация <span className="facts-tab-n">{mats.materials.length}</span>
          </button>
        </div>
        <button className="ghost" onClick={() => run(true)} disabled={busy} title="Перечитать (игнорировать кэш)">
          <RefreshCw {...ICON} />
        </button>
      </div>

      {view === "reconcile" && rec && (
        <>
          <div className="reconcile-summary">
            <span style={{ color: "var(--ok)" }}>{rec.matched} совпало</span>
            <span style={{ color: "var(--warn)" }}>{rec.only_in_assembly} нет в спец-ии</span>
            <span style={{ color: "var(--danger)" }}>{rec.only_in_spec} не в сборках</span>
          </div>
          <div className="reconcile-list">
            {rec.items.map((it: ReconcileItem, i) => {
              const meta = STATUS_META[it.status];
              return (
                <div key={i} className="reconcile-row">
                  <span className="reconcile-icon" style={{ color: meta.color }}>{meta.icon}</span>
                  <div className="reconcile-main">
                    <div className="reconcile-name">{it.name}</div>
                    <div className="reconcile-sub">
                      <span style={{ color: meta.color }}>{meta.label}</span>
                      {it.matched_to && it.matched_to !== it.name && <> · «{it.matched_to}»</>}
                      {it.spec_unit && <> · {it.spec_quantity ?? ""} {it.spec_unit}</>}
                      {it.assembly_refs.length > 0 && <> · сборки: {it.assembly_refs.join(", ")}</>}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {view === "spec" && (
        <div className="spec-list">
          {mats.materials.length === 0 && (
            <div className="materials-hint">На листе не найдено таблиц/легенды материалов.</div>
          )}
          {mats.materials.map((m, i) => (
            <div key={i} className="spec-row">
              {m.color_hex && <span className="spec-swatch" style={{ background: m.color_hex }} title={m.color_hex} />}
              <div className="spec-main">
                <div className="spec-name">
                  {m.position && <span className="spec-pos">{m.position}</span>}
                  {m.name}
                </div>
                <div className="spec-sub">
                  {(m.quantity != null || m.unit) && <span>{m.quantity ?? ""} {m.unit ?? ""}</span>}
                  <span className="spec-source">{m.source === "both" ? "табл+цвет" : m.source === "color" ? "цвет" : "табл"}</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
