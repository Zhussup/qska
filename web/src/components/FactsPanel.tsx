import React, { useState } from "react";
import { MapPin, Ruler, Triangle, Search } from "lucide-react";
import type { Pass1Facts } from "../types";

interface Props {
  facts: Pass1Facts | null;
}

const ICON = { size: 12, strokeWidth: 1.75 } as const;
type Tab = "elev" | "dim" | "node";

const CONF_COLOR: Record<string, string> = {
  high: "var(--ok)",
  medium: "var(--warn)",
  low: "var(--danger)",
};

export function FactsPanel({ facts }: Props) {
  const [tab, setTab] = useState<Tab>("elev");
  const [q, setQ] = useState("");

  const elevs = facts?.elevations ?? [];
  const dims = facts?.dimensions ?? [];
  const nodes = facts?.node_markers ?? [];

  if (!facts) {
    return <div className="facts-empty">Нет данных — запусти Extract.</div>;
  }

  const needle = q.trim().toLowerCase();
  const match = (...parts: (string | number | null | undefined)[]) =>
    !needle || parts.some(p => String(p ?? "").toLowerCase().includes(needle));

  return (
    <div className="facts-panel">
      <div className="facts-tabs">
        <button className={"facts-tab" + (tab === "elev" ? " active" : "")} onClick={() => setTab("elev")}>
          <MapPin {...ICON} /> Отметки <span className="facts-tab-n">{elevs.length}</span>
        </button>
        <button className={"facts-tab" + (tab === "dim" ? " active" : "")} onClick={() => setTab("dim")}>
          <Ruler {...ICON} /> Размеры <span className="facts-tab-n">{dims.length}</span>
        </button>
        <button className={"facts-tab" + (tab === "node" ? " active" : "")} onClick={() => setTab("node")}>
          <Triangle {...ICON} /> Узлы <span className="facts-tab-n">{nodes.length}</span>
        </button>
      </div>

      <div className="facts-search">
        <Search size={12} strokeWidth={1.75} />
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Фильтр…"
        />
      </div>

      <div className="facts-list">
        {tab === "elev" && elevs.filter(e => match(e.value, e.points_to)).map((e, i) => (
          <div key={i} className="fact-item">
            <span className="fact-val">{e.value}</span>
            <span className="fact-desc">{e.points_to}</span>
            <span className="fact-dot" style={{ background: CONF_COLOR[e.confidence] ?? "var(--fg-faint)" }}
                  title={`уверенность: ${e.confidence}`} />
          </div>
        ))}
        {tab === "dim" && dims.filter(d => match(d.value_mm, d.between)).map((d, i) => (
          <div key={i} className="fact-item">
            <span className="fact-val">{d.value_mm} мм</span>
            <span className="fact-desc">{d.between}</span>
            <span className="fact-dot" style={{ background: CONF_COLOR[d.confidence] ?? "var(--fg-faint)" }}
                  title={`уверенность: ${d.confidence}`} />
          </div>
        ))}
        {tab === "node" && nodes.filter(n => match(n.marker, n.where, n.labels)).map((n, i) => (
          <div key={i} className="fact-item">
            <span className="fact-val">{n.marker}</span>
            <span className="fact-desc">{n.labels || n.where}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
