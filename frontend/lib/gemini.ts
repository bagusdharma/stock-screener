import { promises as fs } from "fs";
import path from "path";
import { getResults } from "./data";
import { toLite } from "./types";

/** Integrasi Gemini utk web — SATU sumber kebenaran dgn bot Telegram:
 *  - GEMINI_API_KEY dibaca dari env process ATAU ../.env (root project)
 *  - Persona dari ../RULE_BOT.md (file yang sama dgn bot)
 *  - Rantai fallback model identik dgn settings.py GEMINI_MODEL_CHAIN */

const DATA_DIR = process.env.DATA_DIR ?? path.join(process.cwd(), "..");

const MODEL_CHAIN = [
  "gemini-3.5-flash",
  "gemini-3-flash-preview",
  "gemini-3.1-flash-lite",
  "gemini-2.5-flash",
  "gemini-2.5-flash-lite",
];

// Model kena limit di-skip sementara (cermin conversation.py)
const blockUntil = new Map<string, number>();
const BLOCK_RATE_LIMIT_MS = 15 * 60 * 1000;
const BLOCK_NOT_FOUND_MS = 24 * 3600 * 1000;

let cachedKey: string | null = null;

async function getApiKey(): Promise<string> {
  if (process.env.GEMINI_API_KEY) return process.env.GEMINI_API_KEY;
  if (cachedKey) return cachedKey;
  try {
    const env = await fs.readFile(path.join(DATA_DIR, ".env"), "utf-8");
    const m = env.match(/^\s*GEMINI_API_KEY\s*=\s*(.+)\s*$/m);
    if (m) {
      cachedKey = m[1].trim().replace(/^["']|["']$/g, "");
      return cachedKey;
    }
  } catch {
    /* fallthrough */
  }
  return "";
}

async function getPersona(): Promise<string> {
  try {
    const txt = await fs.readFile(path.join(DATA_DIR, "RULE_BOT.md"), "utf-8");
    if (txt.trim()) return txt.trim();
  } catch {
    /* fallthrough */
  }
  return "Kamu adalah StockAI, asisten AI untuk aplikasi screening saham IDX.";
}

const DATA_RULES = `
ATURAN DATA SANGAT PENTING:
- Jawab HANYA berdasarkan data screening di bawah
- DILARANG KERAS mengarang atau mengubah angka skor, ROE, PER, yield
- Salin PERSIS angka dari data — jangan bulatkan, jangan ubah
- DILARANG menyarankan MEMBELI saham berlabel JUAL atau HOLD —
  rekomendasi beli HANYA untuk label STRONG BUY dan BUY
- Saat menyebut dividen, sebutkan NOMINAL rupiah per lembar (DivTTM),
  persen yield hanya pelengkap dalam kurung
- Saham dibeli per LOT (1 lot = 100 lembar); harga lot = harga x 100
- Format jawaban: teks biasa / markdown ringan (**tebal**), TANPA tabel
- LABEL: STRONG BUY >= 90, BUY >= 85, HOLD >= 70, JUAL < 70
`;

async function buildContext(): Promise<string> {
  const cache = await getResults();
  if (!cache.data.length)
    return "Belum ada data screening. User perlu jalankan screening dulu.";
  const lite = cache.data.map(toLite).sort((a, b) => b.skor - a.skor);
  const counts: Record<string, number> = {};
  for (const s of lite) counts[s.label] = (counts[s.label] ?? 0) + 1;

  const lines: string[] = [
    `Data screening ${lite.length} saham IDX (${cache.generated_at}):`,
    `  STRONG BUY: ${counts["STRONG BUY"] ?? 0} | BUY: ${counts["BUY"] ?? 0} | HOLD: ${counts["HOLD"] ?? 0} | JUAL: ${counts["JUAL"] ?? 0}`,
    "",
    `Semua ${lite.length} saham (urut skor tertinggi):`,
  ];
  lite.forEach((s, i) => {
    if (i < 100) {
      lines.push(
        `${s.ticker.replace(".JK", "")}|skor:${s.skor}|${s.label}|harga:${s.price ?? "?"}|lot:${s.harga_lot}` +
          (s.div_ttm > 0
            ? `|DivTTM:Rp${s.div_ttm}/lbr|yield:${(s.yield_ttm ?? 0).toFixed(1)}%`
            : "") +
          (s.div_streak > 0 ? `|div:${s.div_streak}thn` : "") +
          (s.ma ? `|MA:${s.ma}` : ""),
      );
    } else {
      lines.push(
        `${s.ticker.replace(".JK", "")}|${s.skor}|${s.label}|lot:${s.harga_lot}`,
      );
    }
  });
  return lines.join("\n");
}

export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

function thinkingConfig(model: string): Record<string, unknown> | null {
  if (model.startsWith("gemini-2")) return { thinkingBudget: 0 };
  return { thinkingLevel: "low" };
}

export async function askStockAI(
  messages: ChatMsg[],
): Promise<{ reply: string; model: string }> {
  const key = await getApiKey();
  if (!key)
    return {
      reply:
        "GEMINI_API_KEY belum dikonfigurasi. Tambahkan di file .env root project.",
      model: "none",
    };

  const [persona, context] = await Promise.all([getPersona(), buildContext()]);
  const systemText = `${persona}\n${DATA_RULES}\n${context}`;

  const contents = messages.slice(-10).map((m) => ({
    role: m.role === "assistant" ? "model" : "user",
    parts: [{ text: m.content.slice(0, 2000) }],
  }));

  let lastErr = "";
  const now = Date.now();

  for (const model of MODEL_CHAIN) {
    if ((blockUntil.get(model) ?? 0) > now) continue;

    for (const withThinking of [true, false]) {
      const generationConfig: Record<string, unknown> = {
        maxOutputTokens: 2048,
        temperature: 0.3,
      };
      if (withThinking) {
        const tc = thinkingConfig(model);
        if (tc) generationConfig.thinkingConfig = tc;
      }

      try {
        const res = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "x-goog-api-key": key,
            },
            body: JSON.stringify({
              system_instruction: { parts: [{ text: systemText }] },
              contents,
              generationConfig,
            }),
            signal: AbortSignal.timeout(30_000),
          },
        );

        if (res.ok) {
          const data = await res.json();
          const reply: string =
            data?.candidates?.[0]?.content?.parts
              ?.map((p: { text?: string }) => p.text ?? "")
              .join("") ?? "";
          if (reply.trim()) return { reply: reply.trim(), model };
          lastErr = "jawaban kosong";
          break; // model berikutnya
        }

        const body = await res.text();
        lastErr = `${model}: HTTP ${res.status}`;
        if (
          res.status === 400 &&
          body.toLowerCase().includes("thinking") &&
          withThinking
        ) {
          continue; // coba lagi tanpa thinkingConfig
        }
        if (res.status === 429 || res.status === 403) {
          blockUntil.set(model, Date.now() + BLOCK_RATE_LIMIT_MS);
        } else if (res.status === 404) {
          blockUntil.set(model, Date.now() + BLOCK_NOT_FOUND_MS);
        }
        break; // model berikutnya
      } catch (e) {
        lastErr = `${model}: ${e instanceof Error ? e.message : String(e)}`;
        break; // timeout/transport → model berikutnya
      }
    }
  }

  return {
    reply:
      "Layanan AI sedang tidak tersedia (semua model sibuk). Coba lagi sebentar lagi.",
    model: `error: ${lastErr}`,
  };
}
