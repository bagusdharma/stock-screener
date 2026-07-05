import { Scale } from "lucide-react";
import { getResults } from "@/lib/data";
import { toLite } from "@/lib/types";
import { fmtTimestamp } from "@/lib/format";
import { CompareClient } from "@/components/CompareClient";

// ISR: data berubah maks 3x/hari (GitHub Actions) — sajikan dari CDN,
// regenerasi tiap 5 menit. force-dynamic membuat TIAP navigasi menunggu
// SSR penuh + parse JSON 3MB = lag yang terasa di HP.
export const revalidate = 300;

export default async function BandingkanPage() {
  const cache = await getResults();
  const stocks = cache.data.map(toLite).sort((a, b) => b.skor - a.skor);

  return (
    <div>
      <div className="mb-5">
        <h1 className="flex items-center gap-2 text-2xl font-bold tracking-tight">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-orange-500/10 text-orange-600 ring-1 ring-orange-500/25">
            <Scale size={17} aria-hidden />
          </span>
          Bandingkan Budget
        </h1>
        <p className="mt-1 text-sm text-stone-600">
          Lihat saham apa yang bisa dibeli di tiap level budget · data{" "}
          {fmtTimestamp(cache.generated_at)}
        </p>
      </div>

      <CompareClient stocks={stocks} />
    </div>
  );
}
