import { NextResponse } from "next/server";
import { askStockAI, type ChatMsg } from "@/lib/gemini";

/** Produksi (Vercel): set CHAT_URL ke FastAPI Render → chat memakai otak
 *  AI Python yang sama dgn bot Telegram. Tanpa CHAT_URL: jalur Gemini
 *  langsung (dev lokal).
 *
 *  Proteksi (kuota Gemini gratis = aset yang dilindungi):
 *  - CHAT_SECRET dikirim sebagai X-Chat-Secret ke backend (proxy ini
 *    server-side, secret tidak pernah sampai ke browser)
 *  - Rate limit per-IP best-effort (in-memory per instance serverless —
 *    tidak sempurna saat scale-out, tapi menghentikan spam sederhana;
 *    backend Render punya limiter sendiri sebagai lapisan kedua)
 *  - Backend balas 429 → TERUSKAN 429, jangan jatuh ke Gemini langsung
 *    (kalau tidak, fallback jadi jalan pintas melewati rate limit) */
const CHAT_URL = process.env.CHAT_URL;
const CHAT_SECRET = process.env.CHAT_SECRET;

const RATE_MAX = 10; // request per IP per menit
const RATE_WINDOW_MS = 60_000;
const MAX_MESSAGE_LEN = 500; // selaras dgn Pydantic backend
const rateHits = new Map<string, number[]>();

function rateLimited(ip: string): boolean {
  const now = Date.now();
  const hits = (rateHits.get(ip) ?? []).filter(
    (t) => now - t < RATE_WINDOW_MS,
  );
  if (hits.length >= RATE_MAX) {
    rateHits.set(ip, hits);
    return true;
  }
  hits.push(now);
  rateHits.set(ip, hits);
  if (rateHits.size > 5000) {
    for (const [k, v] of rateHits) {
      if (v.every((t) => now - t >= RATE_WINDOW_MS)) rateHits.delete(k);
    }
  }
  return false;
}

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(req: Request) {
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  if (rateLimited(ip)) {
    return NextResponse.json(
      { error: "Terlalu banyak pesan — coba lagi sebentar lagi." },
      { status: 429, headers: { "Retry-After": "60" } },
    );
  }

  let body: { messages?: ChatMsg[] };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body tidak valid" }, { status: 400 });
  }

  const messages = (body.messages ?? []).filter(
    (m) =>
      (m.role === "user" || m.role === "assistant") &&
      typeof m.content === "string" &&
      m.content.trim(),
  );
  if (messages.length === 0 || messages[messages.length - 1].role !== "user") {
    return NextResponse.json(
      { error: "Butuh minimal satu pesan user" },
      { status: 400 },
    );
  }
  if (messages[messages.length - 1].content.length > MAX_MESSAGE_LEN) {
    return NextResponse.json(
      { error: `Pesan maksimal ${MAX_MESSAGE_LEN} karakter` },
      { status: 400 },
    );
  }

  if (CHAT_URL) {
    try {
      const last = messages[messages.length - 1];
      const clientId =
        typeof (body as { clientId?: unknown }).clientId === "string"
          ? ((body as { clientId: string }).clientId).slice(0, 64)
          : "anon";
      const res = await fetch(`${CHAT_URL.replace(/\/$/, "")}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(CHAT_SECRET ? { "X-Chat-Secret": CHAT_SECRET } : {}),
          "X-Forwarded-For": ip,
        },
        body: JSON.stringify({ user_id: clientId, message: last.content }),
        signal: AbortSignal.timeout(60_000),
      });
      if (res.ok) {
        const data = await res.json();
        return NextResponse.json({ reply: data.reply, model: "backend" });
      }
      if (res.status === 429) {
        return NextResponse.json(
          { error: "Terlalu banyak pesan — coba lagi sebentar lagi." },
          { status: 429, headers: { "Retry-After": "60" } },
        );
      }
    } catch {
      /* jatuh ke jalur langsung di bawah */
    }
  }

  const { reply, model } = await askStockAI(messages);
  return NextResponse.json({ reply, model });
}
