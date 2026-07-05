"use client";

import { useEffect, useRef, useState } from "react";
import { Send, Sparkles, User, Loader2 } from "lucide-react";
import type { ChatMsg } from "@/lib/gemini";

const QUICK_PROMPTS = [
  "5 saham rekomendasi terbaik",
  "Saham dividen paling besar apa?",
  "Beda budget 100rb dan 500rb?",
  "Kenapa BBCA layak dibeli?",
];

/** Render markdown ringan: **bold**, baris baru, bullet.
 *  Jawaban dari backend Python berformat HTML Telegram (<b>, <code>) —
 *  dikonversi dulu ke markdown lalu tag sisa dibuang. */
function renderLite(raw: string) {
  const text = raw
    .replace(/<b>([\s\S]*?)<\/b>/g, "**$1**")
    .replace(/<code>([\s\S]*?)<\/code>/g, "$1")
    .replace(/<i>([\s\S]*?)<\/i>/g, "$1")
    .replace(/<[^>]+>/g, "");
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={i} className="font-semibold text-stone-900">
        {p.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

function Bubble({ m }: { m: ChatMsg }) {
  const isUser = m.role === "user";
  return (
    <div
      className={`flex items-end gap-2 ${isUser ? "flex-row-reverse" : ""}`}
    >
      <span
        aria-hidden
        className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${
          isUser
            ? "bg-stone-200 text-stone-600"
            : "bg-gradient-to-b from-violet-500 to-purple-600 text-white shadow-[0_2px_10px_-2px_rgba(124,58,237,0.45)]"
        }`}
      >
        {isUser ? <User size={13} /> : <Sparkles size={13} />}
      </span>
      <div
        className={`max-w-[82%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "rounded-br-md bg-stone-800 text-stone-50 shadow-[0_2px_10px_-4px_rgba(41,37,36,0.4)]"
            : "card-shadow rounded-bl-md border border-[var(--border)] bg-[var(--surface)] text-stone-800"
        }`}
      >
        {isUser ? m.content : renderLite(m.content)}
      </div>
    </div>
  );
}

function getClientId(): string {
  if (typeof window === "undefined") return "ssr";
  let id = localStorage.getItem("stockai-client-id");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("stockai-client-id", id);
  }
  return id;
}

export default function ChatPage() {
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs, busy]);

  // Saran lanjutan: muncul lagi tiap bot selesai menjawab, minus yang
  // sudah pernah ditanya (feedback user: dulu hilang selamanya setelah 1x klik)
  const asked = new Set(
    msgs.filter((m) => m.role === "user").map((m) => m.content),
  );
  const remaining = QUICK_PROMPTS.filter((p) => !asked.has(p));
  const showFollowUp =
    !busy && msgs.length > 0 && msgs[msgs.length - 1].role === "assistant";

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const next: ChatMsg[] = [...msgs, { role: "user", content: q }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next, clientId: getClientId() }),
      });
      const data = await res.json();
      setMsgs([
        ...next,
        {
          role: "assistant",
          content:
            data.reply ?? data.error ?? "Terjadi kesalahan. Coba lagi ya.",
        },
      ]);
    } catch {
      setMsgs([
        ...next,
        {
          role: "assistant",
          content: "Koneksi terputus — periksa server lalu coba lagi.",
        },
      ]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  return (
    <div className="mx-auto flex h-[calc(100dvh-9.5rem)] max-w-2xl flex-col sm:h-[calc(100dvh-8rem)]">
      {/* Header chat */}
      <div className="mb-3 flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-b from-violet-500 to-purple-600 text-white shadow-[0_4px_14px_-5px_rgba(124,58,237,0.5)]">
          <Sparkles size={18} aria-hidden />
        </span>
        <div>
          <h1 className="flex items-center gap-2 text-lg font-bold tracking-tight">
            StockAI
            <span className="rounded-md bg-violet-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-violet-700 ring-1 ring-violet-300">
              Asisten Analis
            </span>
          </h1>
          <p className="text-xs font-medium text-stone-600">
            Analisis 957 saham hasil screening — data asli, bukan karangan
          </p>
        </div>
      </div>

      {/* Area pesan */}
      <div
        className="card-shadow flex-1 space-y-4 overflow-y-auto rounded-2xl border border-[var(--border)] bg-[var(--surface-2)]/50 p-4"
        aria-live="polite"
      >
        {msgs.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <p className="text-sm text-stone-600">
              Mulai dengan salah satu pertanyaan ini, atau ketik sendiri:
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {QUICK_PROMPTS.map((p) => (
                <button
                  key={p}
                  onClick={() => send(p)}
                  className="card-shadow cursor-pointer rounded-full border border-[var(--border)] bg-[var(--surface)] px-3.5 py-2 text-xs font-medium text-stone-700 transition-all duration-200 hover:border-violet-300 hover:text-violet-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 motion-safe:active:scale-95"
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}

        {msgs.map((m, i) => (
          <Bubble key={i} m={m} />
        ))}

        {showFollowUp && (
          <div className="pl-9">
            <p className="mb-2 text-[11px] text-stone-500">
              Ada pertanyaan lain? Tinggal ketik di bawah — langsung kujawab.
              {remaining.length > 0 && " Atau coba:"}
            </p>
            {remaining.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {remaining.map((p) => (
                  <button
                    key={p}
                    onClick={() => send(p)}
                    className="cursor-pointer rounded-full border border-[var(--border)] bg-[var(--surface)] px-3 py-1.5 text-[11px] font-medium text-stone-600 transition-all duration-200 hover:border-violet-300 hover:text-violet-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-500 motion-safe:active:scale-95"
                  >
                    {p}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {busy && (
          <div className="flex items-center gap-2 pl-9 text-xs text-stone-600">
            <Loader2
              size={13}
              aria-hidden
              className="motion-safe:animate-spin"
            />
            StockAI sedang menganalisis…
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Input bar */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="mt-3 flex gap-2"
      >
        <label className="sr-only" htmlFor="chat-input">
          Pertanyaan untuk StockAI
        </label>
        <input
          id="chat-input"
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Tanya soal saham, dividen, budget…"
          disabled={busy}
          className="card-shadow min-h-[44px] w-full rounded-xl border border-[var(--border)] bg-[var(--surface)] px-4 text-sm text-stone-900 outline-none transition-colors duration-200 placeholder:text-stone-400 hover:border-[var(--border-strong)] focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          aria-label="Kirim pertanyaan"
          className="grid h-11 w-11 shrink-0 cursor-pointer place-items-center rounded-xl bg-stone-900 text-white transition-all duration-200 hover:bg-stone-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-500 disabled:cursor-not-allowed disabled:opacity-40 motion-safe:active:scale-95"
        >
          {busy ? (
            <Loader2 size={17} aria-hidden className="motion-safe:animate-spin" />
          ) : (
            <Send size={17} aria-hidden />
          )}
        </button>
      </form>
      <p className="mt-2 text-center text-[10px] text-stone-500">
        Jawaban berdasarkan data screening — bukan rekomendasi investasi resmi.
      </p>
    </div>
  );
}
