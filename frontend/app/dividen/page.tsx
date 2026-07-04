import { Coins } from "lucide-react";
import { getResults } from "@/lib/data";
import { toLite } from "@/lib/types";
import { fmtTimestamp } from "@/lib/format";
import { DividenList } from "@/components/DividenList";

export const dynamic = "force-dynamic";

export default async function DividenPage() {
  const cache = await getResults();
  const divStocks = cache.data
    .map(toLite)
    .filter((s) => s.div_ttm > 0 || (s.yield_ttm ?? 0) > 0);

  return (
    <div>
      <div className="mb-5 flex items-start justify-between gap-2">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald-500/10 text-emerald-600 ring-1 ring-emerald-600/20">
              <Coins size={17} aria-hidden />
            </span>
            Top Dividen
          </h1>
          <p className="mt-1 text-sm text-stone-600">
            Nominal rupiah per lembar &amp; per lot per tahun · data{" "}
            {fmtTimestamp(cache.generated_at)}
          </p>
        </div>
      </div>

      <DividenList stocks={divStocks} />

      <p className="mt-6 text-center text-xs text-stone-500">
        Yield historis (TTM) — bukan jaminan dividen berikutnya. DYOR.
      </p>
    </div>
  );
}
