import React, { useState } from "react";
import { AlertTriangle, ChevronRight, Wand2, Crosshair } from "lucide-react";
import type { GapItem } from "../types";

interface Props {
  gaps: GapItem[];
  selectedGap: GapItem | null;
  onSelectGap: (g: GapItem | null) => void;
  /** Re-ask the cheap model for this single field (only if a bbox exists). */
  onReask?: (g: GapItem) => void;
  busy?: boolean;
}

const ICON = { size: 12, strokeWidth: 1.75 } as const;

const SEV_LABEL: Record<string, string> = {
  high: "Критично",
  medium: "Средне",
  low: "Незнач.",
};
const SEV_COLOR: Record<string, string> = {
  high: "var(--danger)",
  medium: "var(--warn)",
  low: "var(--ok)",
};

export function GapPanel({ gaps, selectedGap, onSelectGap, onReask, busy }: Props) {
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!gaps.length) {
    return (
      <div className="gap-panel-empty">
        Пропусков нет — все ключевые поля заполнены.
      </div>
    );
  }

  // High → medium → low for triage.
  const order = { high: 0, medium: 1, low: 2 } as const;
  const sorted = [...gaps].sort((a, b) => order[a.severity] - order[b.severity]);

  return (
    <div className="gap-panel">
      {sorted.map((g, i) => {
        const isSel = selectedGap === g;
        const isOpen = expanded === i;
        return (
          <div
            key={`${g.field}-${i}`}
            className={"gap-row" + (isSel ? " selected" : "")}
          >
            <button
              className="gap-row-head"
              onClick={() => {
                setExpanded(isOpen ? null : i);
                onSelectGap(isSel ? null : g);
              }}
              title={g.field}
            >
              <ChevronRight
                {...ICON}
                style={{ transform: isOpen ? "rotate(90deg)" : "none", transition: "transform .1s", flexShrink: 0 }}
              />
              <AlertTriangle {...ICON} style={{ color: SEV_COLOR[g.severity], flexShrink: 0 }} />
              <span className="gap-row-field">{g.field}</span>
              <span className="gap-row-sev" style={{ color: SEV_COLOR[g.severity] }}>
                {SEV_LABEL[g.severity] ?? g.severity}
              </span>
            </button>

            {isOpen && (
              <div className="gap-row-body">
                <div className="gap-row-reason">
                  <span className="gap-kv-key">причина</span> {g.reason}
                </div>
                {g.suggestion && (
                  <div className="gap-row-suggestion">
                    <span className="gap-kv-key">что делать</span> {g.suggestion}
                  </div>
                )}
                <div className="gap-row-actions">
                  {g.bbox_xyxy ? (
                    <>
                      <span className="gap-badge ok">
                        <Crosshair {...ICON} /> область найдена
                      </span>
                      {onReask && (
                        <button className="ghost" disabled={busy} onClick={() => onReask(g)}>
                          <Wand2 {...ICON} /> Переспросить
                        </button>
                      )}
                    </>
                  ) : (
                    <span className="gap-badge">
                      нет координат — выдели зону на чертеже вручную
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
