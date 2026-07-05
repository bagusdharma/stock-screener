import { Eye, TrendingDown } from "lucide-react";
import { getResults } from "@/lib/data";
import { toLite } from "@/lib/types";
import { fmtTimestamp } from "@/lib/format";
import { StockList } from "@/components/StockList";

// ISR: data berubah maks 3x/hari (GitHub Actions) — sajikan dari CDN,
// regenerasi tiap 5 menit. force-dynamic membuat TIAP navigasi menunggu
// SSR penuh + parse JSON 3MB = lag yang terasa di HP.
export const revalidate = 300;

export default async function WatchlistPage() {
  const cache = await getResults();
  const hold = cache.data
    .filter((r) => r.label === "HOLD")
    .map(toLite)
    .sort((a, b) => b.skor - a.skor);
  const jual = cache.data
    .filter((r) => r.label === "JUAL")
    .map(toLite)
    .sort((a, b) => b.skor - a.skor);

  return (
    <div>
      <div className="mb-5">
        <h1 className="text-2xl font-bold tracking-tight">Watchlist</h1>
        <p className="mt-0.5 text-sm text-stone-600">
          Pantau saham HOLD &amp; hindari SELL · data{" "}
          {fmtTimestamp(cache.generated_at)}
        </p>
      </div>

      <section className="mb-8">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-amber-600">
          <Eye size={15} aria-hidden />
          HOLD
          <span className="tnum rounded-md bg-amber-500/10 px-1.5 py-0.5 font-mono text-[11px] text-amber-600 ring-1 ring-amber-600/25">
            {hold.length}
          </span>
          <span className="text-xs font-normal text-stone-600">
            skor cukup, belum layak beli
          </span>
        </h2>
        {hold.length === 0 ? (
          <p className="text-sm text-stone-600">Tidak ada saham HOLD.</p>
        ) : (
          <StockList stocks={hold} showFilters={hold.length > 20} />
        )}
      </section>

      <section>
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-rose-600">
          <TrendingDown size={15} aria-hidden />
          SELL
          <span className="tnum rounded-md bg-rose-500/10 px-1.5 py-0.5 font-mono text-[11px] text-rose-600 ring-1 ring-rose-600/25">
            {jual.length}
          </span>
          <span className="text-xs font-normal text-stone-600">
            skor rendah, waspadai risiko
          </span>
        </h2>
        <StockList stocks={jual} showFilters={jual.length > 20} />
      </section>
    </div>
  );
}
