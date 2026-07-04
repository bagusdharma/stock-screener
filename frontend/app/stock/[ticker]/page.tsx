import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ArrowLeft,
  Banknote,
  BarChart3,
  CheckCircle2,
  Gauge,
  LineChart,
  AlertTriangle,
} from "lucide-react";
import { getResults } from "@/lib/data";
import {
  displayTicker,
  fmtMarketCap,
  fmtNum,
  fmtRp,
  fmtTimestamp,
} from "@/lib/format";
import { LabelBadge, ScoreRing } from "@/components/ScoreBadge";

export const dynamic = "force-dynamic";

function Metric({ k, v, hi = false }: { k: string; v: string; hi?: boolean }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-2.5">
      <p className="text-[10px] font-medium uppercase tracking-wider text-stone-600">
        {k}
      </p>
      <p
        className={`tnum mt-1 font-mono text-sm font-semibold ${hi ? "text-emerald-600" : "text-stone-900"}`}
      >
        {v}
      </p>
    </div>
  );
}

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--surface)]/60 p-4">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-stone-800">
        <span className="text-orange-500">{icon}</span>
        {title}
      </h2>
      {children}
    </section>
  );
}

const BAR_COLORS = ["#059669", "#0284c7", "#7c3aed", "#d97706", "#db2777"];

export default async function StockDetailPage({
  params,
}: {
  params: { ticker: string };
}) {
  const ticker = decodeURIComponent(params.ticker);
  const cache = await getResults();
  const r = cache.data.find((x) => x.ticker === ticker);
  if (!r) notFound();

  const dy = r.yield_ttm ?? 0;
  const divTtm =
    r.div_amount_ttm && r.div_amount_ttm > 0
      ? r.div_amount_ttm
      : dy > 0 && r.price
        ? (dy / 100) * r.price
        : 0;
  const lot = r.harga_lot || (r.price ? Math.round(r.price * 100) : 0);
  const k = r.komponen;

  const KOMPONEN = [
    { nama: "Kualitas", nilai: k?.A_kualitas ?? 0, max: 40 },
    { nama: "Dividen", nilai: k?.B_dividen ?? 0, max: 20 },
    { nama: "Growth", nilai: k?.C_growth ?? 0, max: 20 },
    { nama: "Valuasi", nilai: k?.D_valuasi ?? 0, max: 15 },
    { nama: "Teknikal", nilai: k?.E_teknikal ?? 0, max: 5 },
  ];

  return (
    <div className="space-y-4">
      <Link
        href="/"
        className="inline-flex cursor-pointer items-center gap-1.5 text-sm text-stone-600 transition-colors hover:text-stone-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
      >
        <ArrowLeft size={14} aria-hidden /> Screener
      </Link>

      <div className="relative overflow-hidden rounded-2xl border border-[var(--border)] bg-gradient-to-b from-[var(--surface-2)] to-[var(--surface)] p-5">
        <div className="flex items-center gap-4">
          <ScoreRing skor={r.skor_total} size={64} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-3xl font-bold tracking-tight">
                {displayTicker(r.ticker)}
              </h1>
              <LabelBadge label={r.label} />
            </div>
            <p className="mt-1 truncate text-sm text-stone-600">{r.name}</p>
            <p className="text-xs text-stone-600">
              {r.sector}
              {r.sub_sector ? ` · ${r.sub_sector}` : ""}
            </p>
          </div>
        </div>
        {r.sub_label && (
          <p className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-sm text-stone-800">
            {r.sub_label}
          </p>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Metric k="Harga /lembar" v={fmtRp(r.price)} />
        <Metric k="Harga /lot" v={fmtRp(lot)} />
        <Metric k="Market Cap" v={fmtMarketCap(r.market_cap)} />
      </div>

      <Section icon={<Banknote size={15} aria-hidden />} title="Dividen">
        <div className="grid grid-cols-3 gap-2">
          <Metric
            k="TTM /lembar"
            v={divTtm > 0 ? fmtRp(Math.round(divTtm)) : "–"}
            hi={divTtm > 0}
          />
          <Metric
            k="TTM /lot /tahun"
            v={divTtm > 0 ? fmtRp(Math.round(divTtm * 100)) : "–"}
            hi={divTtm > 0}
          />
          <Metric
            k="Yield · Streak"
            v={`${dy > 0 ? dy.toFixed(1) + "%" : "–"} · ${r.div_streak ?? 0}th`}
          />
        </div>
      </Section>

      <Section icon={<BarChart3 size={15} aria-hidden />} title="Fundamental">
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <Metric k="PER" v={fmtNum(r.pe) + "x"} />
          <Metric k="PBV" v={fmtNum(r.pbv, 2) + "x"} />
          <Metric k="ROE" v={fmtNum(r.roe) + "%"} />
          <Metric k="DER" v={fmtNum(r.der, 2) + "x"} />
          <Metric k="Margin" v={fmtNum(r.net_profit_margin) + "%"} />
          <Metric k="CAGR Rev 3Y" v={fmtNum(r.revenue_cagr_3y) + "%"} />
        </div>
      </Section>

      <Section icon={<LineChart size={15} aria-hidden />} title="Teknikal">
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <Metric k="MA Trend" v={r.teknikal?.ma ?? "–"} />
          <Metric k="MACD" v={r.teknikal?.macd ?? "–"} />
          <Metric k="RSI" v={fmtNum(r.teknikal?.rsi, 0)} />
          <Metric k="MFI" v={fmtNum(r.teknikal?.mfi, 0)} />
          <Metric k="OBV" v={r.teknikal?.obv ?? "–"} />
          <Metric
            k="Momentum 3M"
            v={
              r.teknikal?.momentum != null
                ? `${r.teknikal.momentum > 0 ? "+" : ""}${r.teknikal.momentum.toFixed(1)}%`
                : "–"
            }
          />
        </div>
      </Section>

      <Section icon={<Gauge size={15} aria-hidden />} title="Skor Breakdown">
        <div className="space-y-2.5">
          {KOMPONEN.map((kk, i) => (
            <div key={kk.nama} className="flex items-center gap-3 text-sm">
              <span className="w-[76px] shrink-0 text-xs text-stone-600">
                {kk.nama}
              </span>
              <div
                className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--border)]"
                role="progressbar"
                aria-valuenow={kk.nilai}
                aria-valuemax={kk.max}
                aria-label={`${kk.nama} ${kk.nilai} dari ${kk.max}`}
              >
                <div
                  className="h-full rounded-full transition-[width] duration-500 ease-out"
                  style={{
                    width: `${Math.min(100, (kk.nilai / kk.max) * 100)}%`,
                    background: BAR_COLORS[i],
                  }}
                />
              </div>
              <span className="tnum w-12 shrink-0 text-right font-mono text-xs text-stone-800">
                {kk.nilai}/{kk.max}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-stone-600">
          Skor dasar{" "}
          <span className="tnum font-mono text-stone-600">
            {r.skor_raw ?? "–"}
          </span>{" "}
          → rating{" "}
          <span className="tnum font-mono text-stone-800">{r.skor_total}</span>{" "}
          (kalibrasi persentil) · data{" "}
          <span className="tnum font-mono">
            {Math.round((r.data_completeness ?? 0) * 100)}%
          </span>
          {r.penalti_total < 0 && (
            <span className="text-rose-500">
              {" "}
              · penalti {r.penalti_total}
            </span>
          )}
        </p>
      </Section>

      {r.alasan?.length > 0 && (
        <Section
          icon={<CheckCircle2 size={15} aria-hidden />}
          title={`Mengapa ${r.label}?`}
        >
          <ul className="space-y-2 text-sm text-stone-800">
            {r.alasan.map((a, i) => {
              const neg =
                a.toLowerCase().includes("penalti") ||
                a.toLowerCase().includes("gate") ||
                /: -\d/.test(a);
              return (
                <li key={i} className="flex gap-2.5">
                  {neg ? (
                    <AlertTriangle
                      size={14}
                      aria-hidden
                      className="mt-0.5 shrink-0 text-amber-600"
                    />
                  ) : (
                    <CheckCircle2
                      size={14}
                      aria-hidden
                      className="mt-0.5 shrink-0 text-emerald-600"
                    />
                  )}
                  <span className="leading-snug">{a}</span>
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      <p className="pb-4 text-center text-xs text-stone-400">
        Data {fmtTimestamp(cache.generated_at)} · Bukan rekomendasi investasi
        resmi — DYOR
      </p>
    </div>
  );
}
