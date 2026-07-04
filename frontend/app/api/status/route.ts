import { NextResponse } from "next/server";
import { getStatus } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET() {
  const data = await getStatus();
  return NextResponse.json(data);
}
