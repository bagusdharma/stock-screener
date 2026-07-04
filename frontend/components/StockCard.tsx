import Link from "next/link";
import { ChevronRight, TrendingUp, TrendingDown } from "lucide-react";
import type { StockLite } from "@/lib/types";
import { displayTicker, fmtRp } from "@/lib/format";
import { LabelBadge, ScoreRing } from "./ScoreBadge";

function TrendIcon({ ma }: { ma: string | null }) {
  if (!ma) return null;
  const up = ma === "Uptrend Kuat" || ma === "Di Atas MA50";
  const down = ma === "Downtrend";
  if (!up && !down) return null;
  return up ? (
    <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600">
      <TrendingUp size={12} strokeWidth={2} aria-hidden /> {ma}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[11px] text-rose-500">
      <TrendingDown size={12} strokeWidth={2} aria-hidden /> {ma}
    </span>
  );
}

export function StockCard({ s, rank }: { s: StockLite; rank?: number }) {
  return (
    <Link
      href={`/stock/${encodeURIComponent(s.ticker)}`}
      className="group flex cursor-pointer items-center gap-3 card-shadow rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 transition-all duration-200 hover:border-[var(--border-strong)] hover:bg-[var(--surface-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 motion-safe:active:scale-[0.99]"
    >
      <ScoreRing skor={s.skor} />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {rank !== undefined && (
            <span className="tnum font-mono text-[10px] text-stone-600">
              #{rank}
            </span>
          )}
          <span className="font-semibold tracking-tight">
            {displayTicker(s.ticker)}
          </span>
          <LabelBadge label={s.label} />
        </div>
        <p className="mt-0.5 truncate text-xs font-medium text-stone-600">
          {s.name || s.sector}
        </p>
        <div className="tnum mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-stone-600">
          <span>
            {fmtRp(s.price)}
            <span className="text-stone-600">/lbr</span>
          </span>
          <span>
            {fmtRp(s.harga_lot)}
            <span className="text-stone-600">/lot</span>
          </span>
          {s.div_ttm > 0 && (
            <span className="text-emerald-600">
              Div {fmtRp(s.div_ttm)}
              <span className="text-emerald-500/80">
                {s.yield_ttm ? ` · ${s.yield_ttm.toFixed(1)}%` : ""}
              </span>
            </span>
          )}
          <TrendIcon ma={s.ma} />
        </div>
      </div>

      <ChevronRight
        size={16}
        aria-hidden
        className="shrink-0 text-stone-400 transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-stone-600"
      />
    </Link>
  );
}
