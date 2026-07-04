import { NextResponse } from "next/server";
import { askStockAI, type ChatMsg } from "@/lib/gemini";

/** Produksi (Vercel): set CHAT_URL ke FastAPI Oracle → chat memakai otak
 *  AI Python yang sama dgn bot Telegram. Tanpa CHAT_URL: jalur Gemini
 *  langsung (dev lokal). */
const CHAT_URL = process.env.CHAT_URL;

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(req: Request) {
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

  if (CHAT_URL) {
    try {
      const last = messages[messages.length - 1];
      const clientId =
        typeof (body as { clientId?: unknown }).clientId === "string"
          ? ((body as { clientId: string }).clientId).slice(0, 64)
          : "anon";
      const res = await fetch(`${CHAT_URL.replace(/\/$/, "")}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: clientId, message: last.content }),
        signal: AbortSignal.timeout(60_000),
      });
      if (res.ok) {
        const data = await res.json();
        return NextResponse.json({ reply: data.reply, model: "backend" });
      }
    } catch {
      /* jatuh ke jalur langsung di bawah */
    }
  }

  const { reply, model } = await askStockAI(messages);
  return NextResponse.json({ reply, model });
}
