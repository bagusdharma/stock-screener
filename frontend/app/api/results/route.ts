import { NextResponse } from "next/server";
import { getResults } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET() {
  const data = await getResults();
  return NextResponse.json(data);
}
