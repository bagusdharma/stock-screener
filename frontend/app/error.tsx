"use client";

import { CloudOff } from "lucide-react";

// Error boundary — tampil hanya jika render halaman gagal total (mis. fetch
// data ke GitHub gagal berulang DAN belum ada halaman ISR lama yang bisa
// disajikan). Ini gangguan sesaat, bukan "belum ada data".
export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="rounded-2xl border border-dashed border-[var(--border-strong)] py-24 text-center">
      <CloudOff size={28} aria-hidden className="mx-auto text-stone-400" />
      <p className="mt-3 font-medium">Gangguan sesaat memuat data</p>
      <p className="mt-1 text-sm text-stone-600">
        Data screening tidak bisa diambil saat ini. Coba lagi beberapa detik
        lagi.
      </p>
      <button
        onClick={() => reset()}
        className="mt-5 rounded-lg border border-[var(--border-strong)] px-4 py-2 text-sm font-medium hover:bg-stone-100"
      >
        Coba lagi
      </button>
    </div>
  );
}
