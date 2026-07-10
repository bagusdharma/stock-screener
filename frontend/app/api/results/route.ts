import { NextResponse } from "next/server";
import { getResults } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await getResults();
    return NextResponse.json(data);
  } catch {
    // getResults() melempar hanya jika semua retry gagal & tidak ada
    // data warm — 503 supaya client tahu ini gangguan sesaat, bukan kosong
    return NextResponse.json(
      { error: "Data screening sementara tidak bisa diambil, coba lagi." },
      { status: 503 },
    );
  }
}
