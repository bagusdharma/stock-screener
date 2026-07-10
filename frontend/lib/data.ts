import { promises as fs } from "fs";
import { statSync } from "fs";
import path from "path";
import type { ResultsCache, ScreenStatus } from "./types";

/** Dua mode sumber data (lihat TUGAS2_PLAN.md keputusan #3):
 *  - Lokal (default): baca file JSON dari root project (satu folder di atas
 *    frontend/) — dashboard jalan di PC yang sama dengan bot.
 *  - DATA_URL (produksi Vercel nanti): fetch JSON dari URL. */

const DATA_DIR = process.env.DATA_DIR ?? path.join(process.cwd(), "..");
const DATA_URL = process.env.DATA_URL; // tanpa trailing slash

const EMPTY_RESULTS: ResultsCache = { generated_at: "", total: 0, data: [] };
const EMPTY_STATUS: ScreenStatus = {
  status: "idle",
  message: "Belum pernah dijalankan",
  progress: 0,
  log: [],
  updated_at: null,
};

// Cache in-memory per-mtime — file 3MB+ (957 saham) jangan di-parse tiap request
let resultsCache: { mtime: number; data: ResultsCache } | null = null;

// Hasil sukses terakhir dari DATA_URL (bertahan selama lambda masih warm).
// raw.githubusercontent sesekali 429/timeout dari IP bersama Vercel — tanpa
// fallback ini satu kegagalan fetch = halaman "Belum ada data" ter-bake ke
// cache ISR 300 detik dan dilihat semua pengunjung.
let lastGoodRemote: ResultsCache | null = null;

const FETCH_RETRY_DELAY_MS = [0, 400, 1200];

async function fetchRemoteResults(): Promise<ResultsCache> {
  let lastErr: unknown = null;
  for (const delay of FETCH_RETRY_DELAY_MS) {
    if (delay) await new Promise((r) => setTimeout(r, delay));
    try {
      const res = await fetch(`${DATA_URL}/results_cache.json`, {
        next: { revalidate: 60 },
        signal: AbortSignal.timeout(8000),
      });
      if (!res.ok) {
        lastErr = new Error(`HTTP ${res.status}`);
        continue;
      }
      const data = (await res.json()) as ResultsCache;
      if (!Array.isArray(data.data)) {
        lastErr = new Error("results_cache.json tanpa array data");
        continue;
      }
      if (data.data.length > 0) lastGoodRemote = data;
      return data;
    } catch (err) {
      lastErr = err;
    }
  }
  if (lastGoodRemote) return lastGoodRemote;
  // Lempar, JANGAN return kosong: revalidasi ISR yang error membuat Next
  // tetap menyajikan halaman lama yang masih berisi data — jauh lebih baik
  // daripada menyajikan "Belum ada data screening" padahal data ada.
  throw new Error(
    `Gagal fetch results_cache.json (${FETCH_RETRY_DELAY_MS.length}x): ${lastErr}`,
  );
}

export async function getResults(): Promise<ResultsCache> {
  if (DATA_URL) {
    return fetchRemoteResults();
  }
  const file = path.join(DATA_DIR, "results_cache.json");
  try {
    const mtime = statSync(file).mtimeMs;
    if (resultsCache && resultsCache.mtime === mtime) return resultsCache.data;
    const raw = await fs.readFile(file, "utf-8");
    const data = JSON.parse(raw) as ResultsCache;
    if (!Array.isArray(data.data)) return EMPTY_RESULTS;
    resultsCache = { mtime, data };
    return data;
  } catch {
    return EMPTY_RESULTS;
  }
}

export async function getStatus(): Promise<ScreenStatus> {
  if (DATA_URL) {
    try {
      const res = await fetch(`${DATA_URL}/screen_status.json`, {
        cache: "no-store",
      });
      if (!res.ok) return EMPTY_STATUS;
      return (await res.json()) as ScreenStatus;
    } catch {
      return EMPTY_STATUS;
    }
  }
  try {
    const raw = await fs.readFile(
      path.join(DATA_DIR, "screen_status.json"),
      "utf-8",
    );
    return JSON.parse(raw) as ScreenStatus;
  } catch {
    return EMPTY_STATUS;
  }
}
