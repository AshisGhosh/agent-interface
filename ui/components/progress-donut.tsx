"use client";

import { cn } from "@/lib/utils";

interface ProgressDonutProps {
  pct: number;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

export function ProgressDonut({
  pct,
  size = 26,
  strokeWidth = 3,
  className,
}: ProgressDonutProps) {
  const clamped = Math.max(0, Math.min(100, Math.round(pct)));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);
  const center = size / 2;

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      aria-label={`Progress: ${clamped}%`}
      title={`${clamped}% complete`}
      className={cn(
        "relative inline-flex shrink-0 items-center justify-center text-[9px] font-semibold tabular-nums text-foreground",
        className,
      )}
      style={{ width: size, height: size }}
    >
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="-rotate-90"
        aria-hidden="true"
      >
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          className="stroke-muted"
        />
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className={cn(
            "transition-[stroke-dashoffset] duration-300 ease-out",
            clamped >= 100 ? "stroke-emerald-500" : "stroke-primary",
          )}
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center leading-none">
        {clamped}
      </span>
    </div>
  );
}
