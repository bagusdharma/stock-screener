import type { Metadata, Viewport } from "next";
import Link from "next/link";
import localFont from "next/font/local";
import { CandlestickChart } from "lucide-react";
import { DesktopNav, MobileNav, StatusHeaderLink } from "@/components/NavLinks";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "IDX Screener — Terminal Saham",
  description:
    "Dashboard screening 957 saham IDX — fundamental, dividen, teknikal",
};

export const viewport: Viewport = {
  themeColor: "#faf6f0",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="id">
      <body
        className={`${geistSans.variable} ${geistMono.variable} min-h-dvh font-sans text-stone-900 antialiased`}
      >
        <header className="sticky top-0 z-40 border-b border-[var(--border)] bg-[#faf6f0]/85 backdrop-blur-md">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
            <Link
              href="/"
              className="group flex cursor-pointer items-center gap-2.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500"
            >
              <span className="grid h-8 w-8 place-items-center rounded-lg border border-orange-500/25 bg-gradient-to-b from-orange-500/20 to-rose-500/10 text-orange-600 shadow-[0_2px_12px_-4px_rgba(234,88,12,0.45)]">
                <CandlestickChart size={17} strokeWidth={2} aria-hidden />
              </span>
              <span className="text-[15px] font-semibold tracking-tight">
                IDX{" "}
                <span className="bg-gradient-to-r from-orange-600 to-rose-500 bg-clip-text text-transparent">
                  Screener
                </span>
              </span>
            </Link>
            <div className="flex items-center gap-1">
              <DesktopNav />
              <StatusHeaderLink />
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 pb-28 pt-5 sm:pb-12">
          {children}
        </main>

        <MobileNav />
      </body>
    </html>
  );
}
