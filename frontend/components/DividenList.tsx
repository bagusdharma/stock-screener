"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Crown, Wallet, ChevronRight } from "lucide-react";
import type { StockLite } from "@/lib/types";
import { displayTicker, fmtRp, parseBudgetInput } from "@/lib/format";
import { LabelBadge, ScoreRing } from "./ScoreBadge";

const SORTS = [
  { key: "yield", label: "Yield %" },
  { key: "nominal", label: "Rp /lembar" },
  { key: "streak", label: "Streak" },
] as const;
type SortKey = (typeof SORTS)[number]["key"];
const PAGE = 30;

function DividenCard({ s, budget }: { s: StockLite; budget: number | null }) {
  const affordable = budget !== null && s.harga_lot > 0 && s.harga_lot <= budget;
  return (
    <Link
      href={`/stock/${encodeURIComponent(s.ticker)}`}
      className="card-shadow group flex cursor-pointer items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 transition-all duration-200 hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 motion-safe:active:scale-[0.99]"
    >
      <ScoreRing skor={s.skor} size={42} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-semibold tracking-tight">
            {displayTicker(s.ticker)}
          </span>
          {s.div_streak >= 10 && (
            <span
              className="inline-flex items-center gap-0.5 rounded-md bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-amber-600/25"
              title={`Dividen ${s.div_streak} tahun berturut-turut`}
            >
              <Crown size={9} aria-hidden /> {s.div_streak}th
            </span>
          )}
          <LabelBadge label={s.label} />
        </div>
        <p className="mt-0.5 truncate text-xs font-medium text-stone-600">{s.name}</p>
        <div className="tnum mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-stone-600">
          <span>
            {fmtRp(s.harga_lot)}
            <span className="text-stone-500">/lot</span>
          </span>
          {s.div_streak > 0 && s.div_streak < 10 && (
            <span>{s.div_streak} thn berturut</span>
          )}
          {budget !== null && (
            <span className={affordable ? "text-emerald-600" : "text-rose-500"}>
              {affordable ? "✓ terjangkau" : "✗ di atas budget"}
            </span>
          )}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <p className="tnum font-mono text-sm font-bold text-emerald-600">
          {fmtRp(s.div_ttm)}
          <span className="text-[10px] font-medium text-stone-500">/lbr</span>
        </p>
        <p className="tnum font-mono text-[11px] text-emerald-600/80">
          {fmtRp(s.div_ttm * 100)}
          <span className="text-stone-500">/lot/thn</span>
        </p>
        <p className="tnum font-mono text-[10px] text-stone-600">
          yield {(s.yield_ttm ?? 0).toFixed(1)}%
        </p>
      </div>
      <ChevronRight
        size={15}
        aria-hidden
        className="shrink-0 text-stone-400 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-stone-600"
      />
    </Link>
  );
}

export function DividenList({ stocks }: { stocks: StockLite[] }) {
  const [sort, setSort] = useState<SortKey>("yield");
  const [budgetRaw, setBudgetRaw] = useState("");
  const [limit, setLimit] = useState(PAGE);

  const budget = parseBudgetInput(budgetRaw);

  const sorted = useMemo(() => {
    const arr = [...stocks];
    if (sort === "nominal") arr.sort((a, b) => b.div_ttm - a.div_ttm);
    else if (sort === "streak")
      arr.sort((a, b) => b.div_streak - a.div_streak || (b.yield_ttm ?? 0) - (a.yield_ttm ?? 0));
    else arr.sort((a, b) => (b.yield_ttm ?? 0) - (a.yield_ttm ?? 0));
    if (budget !== null)
      return arr.filter((s) => s.harga_lot > 0 && s.harga_lot <= budget);
    return arr;
  }, [stocks, sort, budget]);

  const shown = sorted.slice(0, limit);

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <div
          role="tablist"
          aria-label="Urutkan berdasarkan"
          className="flex gap-1.5"
        >
          {SORTS.map((s) => (
            <button
              key={s.key}
              role="tab"
              aria-selected={sort === s.key}
              onClick={() => {
                setSort(s.key);
                setLimit(PAGE);
              }}
              className={`cursor-pointer rounded-full px-3 py-1.5 text-[11px] font-semibold transition-all duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 ${
                sort === s.key
                  ? "bg-orange-500/10 text-orange-700 ring-1 ring-orange-500/40"
                  : "bg-[var(--surface)] text-stone-600 ring-1 ring-[var(--border)] hover:text-stone-900"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <label className="relative sm:ml-auto sm:w-52">
          <span className="sr-only">Filter budget per lot</span>
          <Wallet
            size={15}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-stone-500"
          />
          <input
            value={budgetRaw}
            onChange={(e) => {
              setBudgetRaw(e.target.value);
              setLimit(PAGE);
            }}
            placeholder="Budget: 100rb / 1jt"
            className="card-shadow w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] py-2 pl-9 pr-3 text-sm outline-none transition-colors duration-200 placeholder:text-stone-400 hover:border-[var(--border-strong)] focus:border-orange-400 focus:ring-2 focus:ring-orange-500/25"
          />
        </label>
      </div>

      <p aria-live="polite" className="mb-3 text-xs text-stone-600">
        <span className="tnum font-mono">{sorted.length}</span> saham pembagi
        dividen
        {budget !== null && ` · lot ≤ ${fmtRp(budget)}`}
      </p>

      {shown.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--border-strong)] py-16 text-center text-sm text-stone-600">
          Tidak ada saham dividen yang cocok dengan filter.
        </div>
      ) : (
        <div className="grid gap-2 lg:grid-cols-2">
          {shown.map((s) => (
            <DividenCard key={s.ticker} s={s} budget={budget} />
          ))}
        </div>
      )}

      {sorted.length > limit && (
        <button
          onClick={() => setLimit((v) => v + PAGE)}
          className="card-shadow mt-4 w-full cursor-pointer rounded-xl border border-[var(--border)] bg-[var(--surface)] py-3 text-sm font-medium text-stone-700 transition-colors duration-200 hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
        >
          Muat {Math.min(PAGE, sorted.length - limit)} lagi
        </button>
      )}
    </div>
  );
}
