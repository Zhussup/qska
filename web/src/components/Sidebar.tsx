import React, { useRef, useState } from "react";
import type { Drawing } from "../types";
import { api } from "../api";

interface Props {
  drawings: Drawing[];
  selected: string | null;
  onSelect: (d: Drawing) => void;
  onRefresh?: () => void;
}

/** Strip a trailing `_page\d+` and try to surface "<project> · лист N". */
function shortName(filename: string): { project: string; sheet: string } {
  const stem = filename.replace(/\.png$/i, "").replace(/_page\d+$/i, "");
  // If the stem ends with a sheet marker, surface it separately.
  const m = stem.match(/^(.*?)([_\- ]?лист[_\- ]?\d+|[_\- ]?л\.\s*\d+|[_\- ]?sheet[_\- ]?\d+)/i);
  if (m) {
    return { project: m[1].replace(/[_\- ]+$/g, "").trim() || stem, sheet: m[2].trim() };
  }
  // Otherwise: split on "_" — first segment is the project, rest is the rest.
  const parts = stem.split(/[_\-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return { project: parts[0], sheet: parts.slice(1).join(" ") };
  }
  return { project: stem, sheet: "" };
}

export function Sidebar({ drawings, selected, onSelect, onRefresh }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handleFileChange(ev: React.ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    ev.target.value = "";
    setUploading(true);
    setUploadError(null);
    try {
      await api.uploadPdf(file);
      onRefresh?.();
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  // Group by dataset for readability.
  const byDs = new Map<string, Drawing[]>();
  for (const d of drawings) {
    if (!byDs.has(d.dataset)) byDs.set(d.dataset, []);
    byDs.get(d.dataset)!.push(d);
  }
  return (
    <div className="sidebar">
      <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
        <button
          className="ghost"
          style={{ width: "100%", textAlign: "left", fontSize: 12 }}
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
          title="Загрузить PDF-проект и разбить на страницы"
        >
          {uploading ? "⏳ Загрузка…" : "⬆ Загрузить PDF"}
        </button>
        {uploadError && (
          <div style={{ color: "var(--err, #c00)", fontSize: 11, marginTop: 4 }}>
            {uploadError}
          </div>
        )}
      </div>
      {[...byDs.entries()].map(([ds, items]) => (
        <div key={ds}>
          <div className="sidebar-group">
            <span className="sidebar-group-name">{ds}</span>
            <span className="sidebar-group-count">{items.length}</span>
          </div>
          {items.map((d) => {
            const { project, sheet } = shortName(d.filename);
            return (
              <div
                key={d.drawing_id}
                className={"sidebar-item" + (d.drawing_id === selected ? " selected" : "")}
                onClick={() => onSelect(d)}
                title={d.filename}
              >
                <div className="name">{project}</div>
                {sheet && <div className="sheet">{sheet}</div>}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
