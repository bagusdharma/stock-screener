"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Plus, Sparkle, X } from "lucide-react";
import type { StockLite } from "@/lib/types";
import { displayTicker, fmtRp, labelStyle, parseBudgets } from "@/lib/format";
import { LabelBadge } from "./ScoreBadge";

const PRESETS = [100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000];

function MiniRow({ s }: { s: StockLite }) {
  return (
    <Link
      href={`/stock/${encodeURIComponent(s.ticker)}`}
      className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors duration-150 hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
    >
      <span
        className={`tnum w-7 shrink-0 text-center font-mono text-xs font-bold ${labelStyle(s.label).text}`}
      >
        {s.skor}
      </span>
      <span className="w-14 shrink-0 font-semibold tracking-tight">
        {displayTicker(s.ticker)}
      </span>
      <LabelBadge label={s.label} />
      <span className="tnum ml-auto shrink-0 font-mono text-[11px] text-stone-600">
        {fmtRp(s.harga_lot)}/lot
      </span>
    </Link>
  );
}

export function CompareClient({ stocks }: { stocks: StockLite[] }) {
  const [selected, setSelected] = useState<number[]>([100_000, 500_000]);
  const [custom, setCustom] = useState("");

  function toggle(b: number) {
    setSelected((cur) =>
      cur.includes(b)
        ? cur.filter((x) => x !== b)
        : [...cur, b].sort((x, y) => x - y).slice(0, 4),
    );
  }

  function addCustom() {
    const parsed = parseBudgets(custom);
    if (parsed.length) {
      setSelected((cur) =>
        [...new Set([...cur, ...parsed])].sort((x, y) => x - y).slice(0, 4),
      );
      setCustom("");
    }
  }

  const sections = useMemo(() => {
    // stocks sudah urut skor desc dari server
    let prev = new Set<string>();
    let prevBudget: number | null = null;
    return selected.map((b) => {
      const afford = stocks.filter(
        (s) =>
          s.harga_lot > 0 &&
          s.harga_lot <= b &&
          (s.label === "STRONG BUY" || s.label === "BUY" || s.label === "HOLD"),
      );
      const newly = afford.filter((s) => !prev.has(s.ticker));
      const section = {
        budget: b,
        count: afford.length,
        top: afford.slice(0, 5),
        newly: prevBudget === null ? [] : newly.slice(0, 8),
        prevBudget,
      };
      prev = new Set(afford.map((s) => s.ticker));
      prevBudget = b;
      return section;
    });
  }, [stocks, selected]);

  return (
    <div>
      {/* Pemilih budget */}
      <div className="card-shadow mb-5 rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4">
        <p className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-stone-600">
          Pilih 2–4 budget untuk dibandingkan
        </p>
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((b) => {
            const active = selected.includes(b);
            return (
              <button
                key={b}
                onClick={() => toggle(b)}
                aria-pressed={active}
                className={`cursor-pointer rounded-full px-3 py-1.5 text-xs font-semibold transition-all duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 ${
                  active
                    ? "bg-orange-500 text-white shadow-[0_2px_10px_-3px_rgba(234,88,12,0.5)]"
                    : "bg-[var(--surface-2)] text-stone-700 ring-1 ring-[var(--border)] hover:ring-[var(--border-strong)]"
                }`}
              >
                {fmtRp(b)}
              </button>
            );
          })}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            addCustom();
          }}
          className="mt-3 flex gap-2"
        >
          <label className="sr-only" htmlFor="custom-budget">
            Budget kustom
          </label>
          <input
            id="custom-budget"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="Kustom: 750rb / 1.5jt / 300000"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--surface-2)]/60 px-3 py-2 text-sm outline-none transition-colors duration-200 placeholder:text-stone-400 focus:border-orange-400 focus:ring-2 focus:ring-orange-500/25"
          />
          <button
            type="submit"
            aria-label="Tambah budget kustom"
            className="grid h-10 w-10 shrink-0 cursor-pointer place-items-center rounded-lg bg-[var(--surface-2)] text-stone-700 ring-1 ring-[var(--border)] transition-colors duration-200 hover:text-orange-600 hover:ring-orange-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
          >
            <Plus size={16} aria-hidden />
          </button>
        </form>
        {selected.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-stone-500">Dibandingkan:</span>
            {selected.map((b) => (
              <button
                key={b}
                onClick={() => toggle(b)}
                aria-label={`Hapus ${fmtRp(b)}`}
                className="inline-flex cursor-pointer items-center gap-1 rounded-full bg-orange-500/10 px-2.5 py-1 text-[11px] font-semibold text-orange-700 ring-1 ring-orange-500/30 transition-colors hover:bg-orange-500/20"
              >
                {fmtRp(b)} <X size={10} aria-hidden />
              </button>
            ))}
          </div>
        )}
      </div>

      {selected.length < 2 ? (
        <div className="rounded-xl border border-dashed border-[var(--border-strong)] py-14 text-center text-sm text-stone-600">
          Pilih minimal 2 budget untuk melihat perbandingannya.
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {sections.map((sec) => (
            <section
              key={sec.budget}
              className="card-shadow rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4"
            >
              <div className="mb-2 flex items-baseline justify-between">
                <h2 className="tnum font-mono text-lg font-bold text-stone-900">
                  {fmtRp(sec.budget)}
                </h2>
                <span className="text-xs text-stone-600">
                  <span className="tnum font-mono font-semibold text-stone-800">
                    {sec.count}
                  </span>{" "}
                  saham layak
                </span>
              </div>

              {sec.top.length === 0 ? (
                <p className="py-6 text-center text-xs text-stone-500">
                  Tidak ada saham BUY/HOLD terjangkau.
                </p>
              ) : (
                <div className="-mx-2">
                  {sec.top.map((s) => (
                    <MiniRow key={s.ticker} s={s} />
                  ))}
                </div>
              )}

              {sec.prevBudget !== null && (
                <div className="mt-3 border-t border-dashed border-[var(--border)] pt-2.5">
                  {sec.newly.length > 0 ? (
                    <p className="flex flex-wrap items-center gap-1 text-[11px] leading-relaxed text-stone-600">
                      <Sparkle
                        size={11}
                        aria-hidden
                        className="text-orange-500"
                      />
                      <span className="font-medium text-orange-700">
                        Baru terjangkau
                      </span>
                      vs {fmtRp(sec.prevBudget)}:
                      {sec.newly.map((s) => (
                        <Link
                          key={s.ticker}
                          href={`/stock/${encodeURIComponent(s.ticker)}`}
                          className="cursor-pointer font-semibold text-stone-800 underline decoration-orange-300 underline-offset-2 hover:text-orange-700"
                        >
                          {displayTicker(s.ticker)}
                        </Link>
                      ))}
                    </p>
                  ) : (
                    <p className="text-[11px] text-stone-500">
                      Tidak ada saham baru dibanding {fmtRp(sec.prevBudget)}.
                    </p>
                  )}
                </div>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
