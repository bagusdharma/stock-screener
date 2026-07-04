/** Cermin persis struktur results_cache.json & screen_status.json.
 *  Semua angka nullable — sumber data bisa punya field kosong. */

export interface Komponen {
  A_kualitas: number;
  B_dividen: number;
  C_growth: number;
  D_valuasi: number;
  E_teknikal: number;
}

export interface Teknikal {
  rsi: number | null;
  macd: string | null;
  ma: string | null;
  vol_trend: string | null;
  momentum: number | null;
  mfi: number | null;
  obv: string | null;
}

export type Label = "STRONG BUY" | "BUY" | "HOLD" | "JUAL";

export interface StockResult {
  ticker: string;
  name: string;
  sector: string;
  sub_sector: string;
  price: number | null;
  harga_lot: number;
  pe: number | null;
  pbv: number | null;
  roe: number | null;
  der: number | null;
  net_profit_margin: number | null;
  market_cap: number | null;
  yield_ttm: number | null;
  div_streak: number | null;
  div_amount_ttm: number | null;
  revenue_cagr_3y: number | null;
  earnings_cagr_3y: number | null;
  skor_total: number;
  skor_raw?: number;
  label: Label;
  sub_label: string;
  komponen: Komponen;
  penalti_total: number;
  penalti_detail: string[];
  alasan: string[];
  data_completeness: number;
  teknikal: Teknikal;
  stale_fields?: string[];
}

export interface ResultsCache {
  generated_at: string;
  total: number;
  data: StockResult[];
}

export interface ScreenStatus {
  status: "idle" | "running" | "done" | "error";
  message: string;
  progress: number;
  log: string[];
  updated_at: string | null;
}

/** DTO ramping untuk list (957 saham — jangan kirim record penuh ke client) */
export interface StockLite {
  ticker: string;
  name: string;
  sector: string;
  skor: number;
  label: Label;
  sub_label: string;
  price: number | null;
  harga_lot: number;
  yield_ttm: number | null;
  div_ttm: number;
  div_streak: number;
  ma: string | null;
  rsi: number | null;
}

export function toLite(r: StockResult): StockLite {
  const dy = r.yield_ttm ?? 0;
  const divTtm =
    r.div_amount_ttm && r.div_amount_ttm > 0
      ? r.div_amount_ttm
      : dy > 0 && r.price
        ? (dy / 100) * r.price
        : 0;
  return {
    ticker: r.ticker,
    name: r.name,
    sector: r.sector,
    skor: r.skor_total,
    label: r.label,
    sub_label: r.sub_label,
    price: r.price,
    harga_lot: r.harga_lot || (r.price ? Math.round(r.price * 100) : 0),
    yield_ttm: r.yield_ttm,
    div_ttm: Math.round(divTtm),
    div_streak: r.div_streak ?? 0,
    ma: r.teknikal?.ma ?? null,
    rsi: r.teknikal?.rsi ?? null,
  };
}
