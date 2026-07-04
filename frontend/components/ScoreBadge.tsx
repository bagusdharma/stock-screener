import {
  ChevronsUp,
  TrendingUp,
  MoveRight,
  TrendingDown,
  type LucideIcon,
} from "lucide-react";
import { displayLabel, labelStyle } from "@/lib/format";

/** Ikon semantik finansial per label — arah pergerakan yang intuitif */
const LABEL_ICON: Record<string, LucideIcon> = {
  "STRONG BUY": ChevronsUp,
  BUY: TrendingUp,
  HOLD: MoveRight,
  JUAL: TrendingDown,
};

export function LabelBadge({ label }: { label: string }) {
  const s = labelStyle(label);
  const Icon = LABEL_ICON[label] ?? TrendingDown;
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md py-0.5 pl-1.5 pr-2 text-[10px] font-bold uppercase tracking-wide ring-1 ${s.badge}`}
    >
      <Icon size={11} strokeWidth={2.75} aria-hidden className="shrink-0" />
      {displayLabel(label)}
    </span>
  );
}

/** Donut skor SVG — progress ring dgn warna semantik label */
export function ScoreRing({
  skor,
  size = 46,
}: {
  skor: number;
  size?: number;
}) {
  const stroke = 3.5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, skor));
  const color =
    skor >= 90
      ? "#059669"
      : skor >= 85
        ? "#0284c7"
        : skor >= 70
          ? "#d97706"
          : "#a8a29e";
  return (
    <span
      className="relative grid shrink-0 place-items-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Skor ${skor} dari 100`}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--border)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct / 100)}
          className="transition-[stroke-dashoffset] duration-500 ease-out"
        />
      </svg>
      <span
        className="tnum absolute font-mono text-[13px] font-bold"
        style={{ color }}
      >
        {skor}
      </span>
    </span>
  );
}
