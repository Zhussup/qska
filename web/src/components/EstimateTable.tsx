import React from "react";
import type { EstimateResult } from "../types";
import { api } from "../api";

interface Props {
  drawingId: string;
  result: EstimateResult | null;
  onResult: (r: EstimateResult) => void;
  hasPass2?: boolean;
}

export function EstimateTable({ drawingId, result, onResult, hasPass2 = false }: Props) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.estimate(drawingId);
      onResult(r);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function exportXlsx() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.estimateExport(drawingId);
      alert(`Сохранено: ${r.path}\n\nИтого: ${r.total} ${r.currency}`);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!result) {
    return (
      <div>
        <button className="primary" onClick={run} disabled={busy || !hasPass2}
                title={hasPass2 ? undefined : "Сначала запусти Extract"}>
          {busy ? <><span className="spinner" /> считаю…</> : "▶ Рассчитать смету"}
        </button>
        {!hasPass2 && <div style={{ color: "var(--fg-faint)", marginTop: 6, fontSize: 12 }}>Сначала запусти Extract</div>}
        {err && <div style={{ color: "var(--danger)", marginTop: 8 }}>{err}</div>}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        <button onClick={run} disabled={busy}>↻ Пересчитать</button>
        <button className="primary" onClick={exportXlsx} disabled={busy}>⬇ Excel</button>
      </div>
      <table className="estimate-table">
        <thead>
          <tr>
            <th>ГЭСН/ФЕР</th>
            <th>Описание</th>
            <th>Ед.</th>
            <th className="num">Кол-во</th>
            <th className="num">Цена</th>
            <th className="num">Σ</th>
          </tr>
        </thead>
        <tbody>
          {result.lines.map((ln, i) => (
            <tr key={i}>
              <td style={{ fontFamily: "monospace", color: "var(--code)" }}>{ln.gesn_code}</td>
              <td>{ln.description}</td>
              <td>{ln.unit}</td>
              <td className="num">{ln.quantity.toFixed(2)}</td>
              <td className="num">{ln.unit_price?.toFixed(0) ?? "—"}</td>
              <td className="num">{ln.total?.toFixed(2) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={5}>ИТОГО</td>
            <td className="num">{result.total.toFixed(2)} {result.currency}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
