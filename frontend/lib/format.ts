import type { Label } from "./types";

/** Threshold skala kalibrasi 2026-07-04 — HARUS sama dgn settings.py */
export const SKOR_STRONG_BUY = 90;
export const SKOR_BUY = 85;
export const SKOR_HOLD = 70;

export function fmtRp(v: number | null | undefined): string {
  if (!v || v === 0) return "–";
  return "Rp " + Math.round(v).toLocaleString("id-ID");
}

export function fmtNum(v: number | null | undefined, d = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return v.toFixed(d);
}

export function displayTicker(t: string): string {
  return t.replace(".JK", "");
}

export function fmtMarketCap(v: number | null | undefined): string {
  if (!v) return "–";
  if (v >= 1e12) return `Rp ${(v / 1e12).toFixed(1)} T`;
  if (v >= 1e9) return `Rp ${(v / 1e9).toFixed(1)} M`;
  return fmtRp(v);
}

export function fmtTimestamp(iso: string | null | undefined): string {
  if (!iso) return "–";
  try {
    const d = new Date(iso);
    return d.toLocaleString("id-ID", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "–";
  }
}

/** Label data internal tetap bahasa JSON ("JUAL") — tampilan pakai SELL */
export function displayLabel(label: string): string {
  return label === "JUAL" ? "SELL" : label;
}

export const LABEL_STYLE: Record<Label, { badge: string; dot: string; text: string }> = {
  "STRONG BUY": {
    badge: "bg-emerald-100 text-emerald-800 ring-emerald-400",
    dot: "bg-emerald-600",
    text: "text-emerald-700",
  },
  BUY: {
    badge: "bg-sky-100 text-sky-800 ring-sky-400",
    dot: "bg-sky-600",
    text: "text-sky-700",
  },
  HOLD: {
    badge: "bg-amber-100 text-amber-800 ring-amber-400",
    dot: "bg-amber-600",
    text: "text-amber-700",
  },
  JUAL: {
    badge: "bg-rose-100 text-rose-800 ring-rose-400",
    dot: "bg-rose-600",
    text: "text-rose-700",
  },
};

export function labelStyle(label: string) {
  return LABEL_STYLE[label as Label] ?? LABEL_STYLE.JUAL;
}

/** Parse SATU nominal budget: "100rb", "1jt", "500000", "1.000.000" */
export function parseBudgetInput(raw: string): number | null {
  const low = raw.toLowerCase().replace(/\./g, "").replace(/,/g, "").trim();
  if (!low) return null;
  let m = low.match(/^(\d+)\s*(?:jt|juta)$/);
  if (m) return parseInt(m[1]) * 1_000_000;
  m = low.match(/^(\d+)\s*(?:rb|ribu)$/);
  if (m) return parseInt(m[1]) * 1_000;
  if (/^\d+$/.test(low)) return parseInt(low);
  return null;
}

/** Parse BANYAK nominal: "100rb dan 200rb", "1jt vs 5jt", "100000, 500000" */
export function parseBudgets(raw: string): number[] {
  const low = raw.toLowerCase().replace(/\./g, "");
  const out = new Set<number>();
  for (const m of low.matchAll(/(\d+)\s*(?:jt|juta)/g))
    out.add(parseInt(m[1]) * 1_000_000);
  for (const m of low.matchAll(/(\d+)\s*(?:rb|ribu)/g))
    out.add(parseInt(m[1]) * 1_000);
  for (const m of low.matchAll(/\b(\d{5,})\b/g)) {
    const v = parseInt(m[1]);
    if (v >= 50_000 && v <= 100_000_000) out.add(v);
  }
  return [...out].sort((a, b) => a - b);
}
