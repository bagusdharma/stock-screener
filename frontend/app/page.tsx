import Link from "next/link";
import { Clock, Database, Sparkles, ArrowRight, Coins, Scale } from "lucide-react";
import { getResults } from "@/lib/data";
import { toLite } from "@/lib/types";
import { displayLabel, fmtTimestamp } from "@/lib/format";
import { StockList } from "@/components/StockList";

// ISR: data berubah maks 3x/hari (GitHub Actions) — sajikan dari CDN,
// regenerasi tiap 5 menit. force-dynamic membuat TIAP navigasi menunggu
// SSR penuh + parse JSON 3MB = lag yang terasa di HP.
export const revalidate = 300;

const SUMMARY = [
  { label: "STRONG BUY", accent: "from-emerald-500", text: "text-emerald-600" },
  { label: "BUY", accent: "from-sky-500", text: "text-sky-600" },
  { label: "HOLD", accent: "from-amber-500", text: "text-amber-600" },
  { label: "JUAL", accent: "from-rose-400", text: "text-rose-600" },
] as const;

export default async function ScreenerPage() {
  const cache = await getResults();
  const stocks = cache.data.map(toLite).sort((a, b) => b.skor - a.skor);

  const counts: Record<string, number> = {};
  for (const s of stocks) counts[s.label] = (counts[s.label] ?? 0) + 1;

  if (stocks.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-[var(--border-strong)] py-24 text-center">
        <Database size={28} aria-hidden className="mx-auto text-stone-400" />
        <p className="mt-3 font-medium">Belum ada data screening</p>
        <p className="mt-1 text-sm text-stone-600">
          Jalankan <span className="font-mono text-stone-600">/screen</span> dari
          bot Telegram terlebih dulu.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Screener</h1>
          <p className="mt-0.5 text-sm text-stone-600">
            <span className="tnum font-mono text-stone-800">{cache.total}</span>{" "}
            saham IDX · fundamental + dividen + teknikal
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1 text-[11px] text-stone-600">
          <Clock size={11} aria-hidden />
          {fmtTimestamp(cache.generated_at)}
        </span>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        {SUMMARY.map((s) => (
          <div
            key={s.label}
            className="card-shadow relative overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3.5"
          >
            <span
              aria-hidden
              className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r ${s.accent} to-transparent`}
            />
            <p className={`tnum font-mono text-2xl font-bold ${s.text}`}>
              {counts[s.label] ?? 0}
            </p>
            <p className="mt-0.5 text-[10px] font-bold uppercase tracking-wider text-stone-600">
              {displayLabel(s.label)}
            </p>
          </div>
        ))}
      </div>

      <Link
        href="/chat"
        className="card-shadow group mb-6 flex cursor-pointer items-center gap-3.5 rounded-2xl border border-violet-200 bg-[var(--surface)] p-4 transition-all duration-200 hover:border-violet-300 hover:bg-violet-50/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-500 motion-safe:active:scale-[0.99]"
      >
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-gradient-to-b from-violet-500 to-purple-600 text-white shadow-[0_3px_12px_-4px_rgba(124,58,237,0.5)]">
          <Sparkles size={19} aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-[15px] font-bold text-stone-900">
            StockAI
            <span className="rounded-md bg-violet-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-violet-700 ring-1 ring-violet-300">
              Asisten Analis
            </span>
          </span>
          <span className="mt-0.5 block truncate text-xs font-medium text-stone-600">
            Rekomendasi, dividen &amp; perbandingan budget dalam satu percakapan
          </span>
        </span>
        <ArrowRight
          size={18}
          aria-hidden
          className="shrink-0 text-violet-400 transition-transform duration-200 group-hover:translate-x-1 group-hover:text-violet-600"
        />
      </Link>

      <div className="mb-6 grid grid-cols-2 gap-2.5">
        <Link
          href="/dividen"
          className="card-shadow group flex cursor-pointer items-center gap-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 transition-all duration-200 hover:border-emerald-400/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
        >
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-emerald-500/10 text-emerald-600">
            <Coins size={15} aria-hidden />
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-stone-900">
              Top Dividen
            </span>
            <span className="block truncate text-[11px] text-stone-600">
              Rp/lembar terbesar
            </span>
          </span>
        </Link>
        <Link
          href="/bandingkan"
          className="card-shadow group flex cursor-pointer items-center gap-2.5 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3 transition-all duration-200 hover:border-orange-400/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
        >
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-orange-500/10 text-orange-600">
            <Scale size={15} aria-hidden />
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-semibold text-stone-900">
              Bandingkan
            </span>
            <span className="block truncate text-[11px] text-stone-600">
              100rb vs 500rb vs 1jt
            </span>
          </span>
        </Link>
      </div>

      <StockList stocks={stocks} ranked />
    </div>
  );
}
