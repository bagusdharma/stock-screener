"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutList,
  Coins,
  Sparkles,
  Scale,
  Eye,
  Activity,
} from "lucide-react";

const NAV_MAIN = [
  { href: "/", label: "Screener", Icon: LayoutList },
  { href: "/dividen", label: "Dividen", Icon: Coins },
  { href: "/chat", label: "StockAI", Icon: Sparkles },
  { href: "/bandingkan", label: "Banding", Icon: Scale },
  { href: "/watchlist", label: "Watchlist", Icon: Eye },
];

const NAV_DESKTOP = [
  ...NAV_MAIN.map((n) =>
    n.href === "/bandingkan" ? { ...n, label: "Bandingkan" } : n,
  ),
  { href: "/status", label: "Status", Icon: Activity },
];

function isActive(path: string, href: string) {
  return href === "/" ? path === "/" : path.startsWith(href);
}

export function DesktopNav() {
  const path = usePathname();
  return (
    <nav className="hidden items-center gap-0.5 md:flex">
      {NAV_DESKTOP.map(({ href, label, Icon }) => {
        const active = isActive(path, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 ${
              active
                ? "bg-orange-500/10 font-medium text-orange-700"
                : "text-stone-600 hover:bg-orange-500/5 hover:text-stone-950"
            }`}
          >
            <Icon size={15} strokeWidth={2} aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

/** Ikon Status utk header mobile (Status tidak muat di bottom-nav maks 5) */
export function StatusHeaderLink() {
  const path = usePathname();
  const active = path.startsWith("/status");
  return (
    <Link
      href="/status"
      aria-label="Status screening"
      aria-current={active ? "page" : undefined}
      className={`grid h-9 w-9 cursor-pointer place-items-center rounded-lg transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-orange-500 md:hidden ${
        active
          ? "bg-orange-500/10 text-orange-600"
          : "text-stone-600 hover:bg-orange-500/5 hover:text-stone-900"
      }`}
    >
      <Activity size={17} aria-hidden />
    </Link>
  );
}

export function MobileNav() {
  const path = usePathname();
  return (
    <nav
      aria-label="Navigasi utama"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-[var(--border)] bg-[#fffdf9]/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
    >
      <div className="grid grid-cols-5">
        {NAV_MAIN.map(({ href, label, Icon }) => {
          const active = isActive(path, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`flex min-h-[52px] cursor-pointer flex-col items-center justify-center gap-1 text-[10px] transition-colors duration-200 ${
                active
                  ? "text-orange-600"
                  : "text-stone-600 active:text-stone-800"
              }`}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.8} aria-hidden />
              <span className={active ? "font-semibold" : ""}>{label}</span>
              <span
                className={`h-0.5 w-7 rounded-full transition-colors ${active ? "bg-orange-500" : "bg-transparent"}`}
              />
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
