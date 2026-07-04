"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  CircleDashed,
  Loader2,
  XCircle,
  Terminal,
} from "lucide-react";
import type { ScreenStatus } from "@/lib/types";
import { fmtTimestamp } from "@/lib/format";

const META: Record<
  ScreenStatus["status"],
  { Icon: typeof Activity; title: string; color: string; spin?: boolean }
> = {
  idle: { Icon: CircleDashed, title: "Belum Ada Screening", color: "text-stone-600" },
  running: { Icon: Loader2, title: "Screening Berjalan", color: "text-orange-600", spin: true },
  done: { Icon: CheckCircle2, title: "Screening Selesai", color: "text-emerald-600" },
  error: { Icon: XCircle, title: "Screening Error", color: "text-rose-600" },
};

export default function StatusPage() {
  const [st, setSt] = useState<ScreenStatus | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const res = await fetch("/api/status", { cache: "no-store" });
        const data = (await res.json()) as ScreenStatus;
        if (alive) setSt(data);
        timer = setTimeout(load, data.status === "running" ? 5000 : 30000);
      } catch {
        timer = setTimeout(load, 15000);
      }
    };
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  if (!st) {
    return (
      <div className="animate-pulse space-y-3" aria-busy="true">
        <div className="h-8 w-48 rounded-lg bg-[var(--surface-2)]" />
        <div className="h-32 rounded-2xl bg-[var(--surface)]" />
        <div className="h-44 rounded-2xl bg-[var(--surface)]" />
      </div>
    );
  }

  const meta = META[st.status] ?? META.idle;
  const { Icon } = meta;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">Status Screening</h1>

      <div className="rounded-2xl border border-[var(--border)] bg-gradient-to-b from-[var(--surface-2)] to-[var(--surface)] p-5">
        <div className="flex items-center gap-4">
          <span
            className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl border border-[var(--border)] bg-[var(--surface-2)] ${meta.color}`}
          >
            <Icon
              size={22}
              aria-hidden
              className={meta.spin ? "motion-safe:animate-spin" : ""}
            />
          </span>
          <div className="min-w-0">
            <p className={`font-semibold ${meta.color}`}>{meta.title}</p>
            <p className="truncate text-sm text-stone-600">
              {st.message || "–"}
            </p>
            {st.updated_at && (
              <p className="mt-0.5 text-xs text-stone-600">
                Update {fmtTimestamp(st.updated_at)}
              </p>
            )}
          </div>
        </div>

        {st.status === "running" && (
          <div className="mt-5">
            <div
              className="h-2 overflow-hidden rounded-full bg-[var(--border)]"
              role="progressbar"
              aria-valuenow={st.progress}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full rounded-full bg-gradient-to-r from-orange-500 to-amber-400 transition-[width] duration-700 ease-out"
                style={{ width: `${st.progress}%` }}
              />
            </div>
            <p className="tnum mt-1.5 text-right font-mono text-xs text-orange-600">
              {st.progress}%
            </p>
          </div>
        )}
      </div>

      {st.log?.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--surface)]">
          <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
            <Terminal size={13} aria-hidden className="text-stone-600" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-stone-600">
              Log Terakhir
            </h2>
          </div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all bg-stone-900 p-4 font-mono text-[11px] leading-relaxed text-stone-400">
            {st.log.slice(-15).join("\n")}
          </pre>
        </div>
      )}

      <p className="text-xs text-stone-600">
        Screening dijalankan dari bot Telegram atau scheduler otomatis —
        halaman ini hanya memantau.
      </p>
    </div>
  );
}
