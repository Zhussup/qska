import React from "react";
import {
  Eye, EyeOff, Ruler, MapPin, Triangle, AlertTriangle, FileCode,
} from "lucide-react";
import type { GapItem, Pass1Facts } from "../types";

export interface LayerState {
  pass1Elevations: boolean;
  pass1Dimensions: boolean;
  pass1Nodes: boolean;
  gapsHigh: boolean;
  gapsMedium: boolean;
  gapsLow: boolean;
}

export const DEFAULT_LAYERS: LayerState = {
  pass1Elevations: true,
  pass1Dimensions: true,
  pass1Nodes: true,
  gapsHigh: true,
  gapsMedium: true,
  gapsLow: true,
};

interface Props {
  layers: LayerState;
  onChange: (next: LayerState) => void;
  pass1Facts?: Pass1Facts | null;
  gaps?: GapItem[];
}

const ICON = { size: 12, strokeWidth: 1.75 } as const;

interface LayerRowProps {
  icon: React.ReactNode;
  label: string;
  count?: number;
  visible: boolean;
  onToggle: () => void;
  swatch: string; // CSS color
}

function LayerRow({ icon, label, count, visible, onToggle, swatch }: LayerRowProps) {
  return (
    <button
      className="layer-row"
      onClick={onToggle}
      title={visible ? "Скрыть слой" : "Показать слой"}
    >
      <span className="layer-row-icon" style={{ color: swatch }}>{icon}</span>
      <span className="layer-row-label">{label}</span>
      {count !== undefined && (
        <span className="layer-row-count">{count}</span>
      )}
      <span className="layer-row-toggle" style={{ color: visible ? "var(--fg)" : "var(--fg-faint)" }}>
        {visible ? <Eye {...ICON} /> : <EyeOff {...ICON} />}
      </span>
    </button>
  );
}

export function LayerPanel({ layers, onChange, pass1Facts, gaps }: Props) {
  const eCount = pass1Facts?.elevations.length ?? 0;
  const dCount = pass1Facts?.dimensions.length ?? 0;
  const nCount = pass1Facts?.node_markers.length ?? 0;
  const high = (gaps ?? []).filter(g => g.severity === "high").length;
  const med  = (gaps ?? []).filter(g => g.severity === "medium").length;
  const low  = (gaps ?? []).filter(g => g.severity === "low").length;

  const total =
    (layers.pass1Elevations ? eCount : 0) +
    (layers.pass1Dimensions ? dCount : 0) +
    (layers.pass1Nodes     ? nCount : 0) +
    (layers.gapsHigh   ? high : 0) +
    (layers.gapsMedium ? med  : 0) +
    (layers.gapsLow    ? low  : 0);

  return (
    <div className="layers-panel">
      <div className="layers-summary">
        <span className="layers-summary-label">Видимо</span>
        <span className="layers-summary-count">{total}</span>
        <button
          className="ghost"
          style={{ fontSize: 10, padding: "2px 8px" }}
          onClick={() => onChange({ ...DEFAULT_LAYERS })}
          title="Включить все слои"
        >
          все
        </button>
        <button
          className="ghost"
          style={{ fontSize: 10, padding: "2px 8px" }}
          onClick={() => onChange({
            pass1Elevations: false, pass1Dimensions: false, pass1Nodes: false,
            gapsHigh: false, gapsMedium: false, gapsLow: false,
          })}
          title="Скрыть все слои"
        >
          ничего
        </button>
      </div>

      <div className="layers-section-title">Данные с чертежа</div>
      <LayerRow
        icon={<MapPin {...ICON} />}
        label="Отметки уровней"
        count={eCount}
        visible={layers.pass1Elevations}
        onToggle={() => onChange({ ...layers, pass1Elevations: !layers.pass1Elevations })}
        swatch="#4493f8"
      />
      <LayerRow
        icon={<Ruler {...ICON} />}
        label="Размеры"
        count={dCount}
        visible={layers.pass1Dimensions}
        onToggle={() => onChange({ ...layers, pass1Dimensions: !layers.pass1Dimensions })}
        swatch="#3fb950"
      />
      <LayerRow
        icon={<Triangle {...ICON} />}
        label="Узлы / марки"
        count={nCount}
        visible={layers.pass1Nodes}
        onToggle={() => onChange({ ...layers, pass1Nodes: !layers.pass1Nodes })}
        swatch="#d29922"
      />

      <div className="layers-section-title">Незаполненные поля</div>
      <LayerRow
        icon={<AlertTriangle {...ICON} />}
        label="Критичные"
        count={high}
        visible={layers.gapsHigh}
        onToggle={() => onChange({ ...layers, gapsHigh: !layers.gapsHigh })}
        swatch="var(--danger)"
      />
      <LayerRow
        icon={<AlertTriangle {...ICON} />}
        label="Средние"
        count={med}
        visible={layers.gapsMedium}
        onToggle={() => onChange({ ...layers, gapsMedium: !layers.gapsMedium })}
        swatch="var(--warn)"
      />
      <LayerRow
        icon={<AlertTriangle {...ICON} />}
        label="Незначительные"
        count={low}
        visible={layers.gapsLow}
        onToggle={() => onChange({ ...layers, gapsLow: !layers.gapsLow })}
        swatch="var(--ok)"
      />
    </div>
  );
}
