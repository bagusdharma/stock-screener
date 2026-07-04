"use client";

import { useMemo, useState } from "react";
import { Search, Wallet, ListFilter } from "lucide-react";
import type { StockLite } from "@/lib/types";
import { displayLabel } from "@/lib/format";
import { StockCard } from "./StockCard";

const LABELS = ["SEMUA", "STRONG BUY", "BUY", "HOLD", "JUAL"] as const;
const PAGE = 50;

/** Parse input budget fleksibel: "100rb", "1jt", "500000", "1.000.000" */
function parseBudget(raw: string): number | null {
  const low = raw.toLowerCase().replace(/\./g, "").replace(/,/g, "").trim();
  if (!low) return null;
  let m = low.match(/^(\d+)\s*(?:jt|juta)$/);
  if (m) return parseInt(m[1]) * 1_000_000;
  m = low.match(/^(\d+)\s*(?:rb|ribu)$/);
  if (m) return parseInt(m[1]) * 1_000;
  if (/^\d+$/.test(low)) return parseInt(low);
  return null;
}

export function StockList({
  stocks,
  defaultLabel = "SEMUA",
  showFilters = true,
  ranked = false,
}: {
  stocks: StockLite[];
  defaultLabel?: (typeof LABELS)[number];
  showFilters?: boolean;
  ranked?: boolean;
}) {
  const [q, setQ] = useState("");
  const [label, setLabel] = useState<(typeof LABELS)[number]>(defaultLabel);
  const [budgetRaw, setBudgetRaw] = useState("");
  const [limit, setLimit] = useState(PAGE);

  const budget = parseBudget(budgetRaw);

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return stocks.filter((s) => {
      if (label !== "SEMUA" && s.label !== label) return false;
      if (budget !== null && (s.harga_lot <= 0 || s.harga_lot > budget))
        return false;
      if (
        ql &&
        !s.ticker.toLowerCase().includes(ql) &&
        !s.name.toLowerCase().includes(ql)
      )
        return false;
      return true;
    });
  }, [stocks, q, label, budget]);

  const shown = filtered.slice(0, limit);
  const inputCls =
    "w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] py-2 pl-9 pr-3 text-sm text-stone-900 outline-none transition-colors duration-200 placeholder:text-stone-600 hover:border-[var(--border-strong)] focus:border-orange-400 focus:ring-2 focus:ring-orange-500/25";

  return (
    <div>
      {showFilters && (
        <div className="mb-4 space-y-2.5">
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="relative flex-1">
              <span className="sr-only">Cari ticker atau nama</span>
              <Search
                size={15}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-600"
              />
              <input
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setLimit(PAGE);
                }}
                placeholder="Cari ticker / nama emiten…"
                className={inputCls}
              />
            </label>
            <label className="relative sm:w-56">
              <span className="sr-only">Filter budget per lot</span>
              <Wallet
                size={15}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-600"
              />
              <input
                value={budgetRaw}
                onChange={(e) => {
                  setBudgetRaw(e.target.value);
                  setLimit(PAGE);
                }}
                placeholder="Budget: 100rb / 1jt"
                inputMode="text"
                className={inputCls}
              />
            </label>
          </div>

          <div
            role="tablist"
            aria-label="Filter label"
            className="flex flex-wrap gap-1.5"
          >
            {LABELS.map((l) => {
              const active = label === l;
              return (
                <button
                  key={l}
                  role="tab"
                  aria-selected={active}
                  onClick={() => {
                    setLabel(l);
                    setLimit(PAGE);
                  }}
                  className={`cursor-pointer rounded-full px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide transition-all duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 ${
                    active
                      ? "bg-orange-500/10 text-orange-700 ring-1 ring-orange-500/40"
                      : "bg-[var(--surface)] text-stone-600 ring-1 ring-[var(--border)] hover:text-stone-900 hover:ring-[var(--border-strong)]"
                  }`}
                >
                  {l === "SEMUA" ? "SEMUA" : displayLabel(l)}
                </button>
              );
            })}
          </div>

          <p
            aria-live="polite"
            className="flex items-center gap-1.5 text-xs text-stone-600"
          >
            <ListFilter size={12} aria-hidden />
            <span className="tnum font-mono">{filtered.length}</span> saham
            {budget !== null && (
              <span className="tnum font-mono">
                · lot ≤ Rp {budget.toLocaleString("id-ID")}
              </span>
            )}
          </p>
        </div>
      )}

      {shown.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--border-strong)] py-16 text-center">
          <p className="text-sm font-medium text-stone-600">
            Tidak ada saham yang cocok
          </p>
          <p className="mt-1 text-xs text-stone-600">
            Coba longgarkan filter budget atau label.
          </p>
        </div>
      ) : (
        <div className="grid gap-2 lg:grid-cols-2">
          {shown.map((s, i) => (
            <StockCard key={s.ticker} s={s} rank={ranked ? i + 1 : undefined} />
          ))}
        </div>
      )}

      {filtered.length > limit && (
        <button
          onClick={() => setLimit((v) => v + PAGE)}
          className="mt-4 w-full cursor-pointer rounded-xl border border-[var(--border)] bg-[var(--surface)] py-3 text-sm font-medium text-stone-800 transition-colors duration-200 hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
        >
          Muat {Math.min(PAGE, filtered.length - limit)} lagi
          <span className="tnum ml-1 font-mono text-stone-600">
            ({filtered.length - limit} tersisa)
          </span>
        </button>
      )}
    </div>
  );
}
