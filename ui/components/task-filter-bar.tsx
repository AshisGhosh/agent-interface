"use client";

import { useEffect, useRef } from "react";
import { Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface TaskFilters {
  query: string;
  priorities: Set<number>;
  tags: Set<string>;
}

export const EMPTY_FILTERS: TaskFilters = {
  query: "",
  priorities: new Set<number>(),
  tags: new Set<string>(),
};

export function filtersActive(f: TaskFilters): boolean {
  return f.query.trim().length > 0 || f.priorities.size > 0 || f.tags.size > 0;
}

interface TaskFilterBarProps {
  filters: TaskFilters;
  onChange: (next: TaskFilters) => void;
  availablePriorities: number[];
  availableTags: string[];
  matchedCount: number;
  totalCount: number;
  className?: string;
}

export function TaskFilterBar({
  filters,
  onChange,
  availablePriorities,
  availableTags,
  matchedCount,
  totalCount,
  className,
}: TaskFilterBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  // `/` focuses the search input, matching the `n` shortcut for new tasks.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      e.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const togglePriority = (p: number) => {
    const next = new Set(filters.priorities);
    if (next.has(p)) next.delete(p);
    else next.add(p);
    onChange({ ...filters, priorities: next });
  };

  const toggleTag = (t: string) => {
    const next = new Set(filters.tags);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onChange({ ...filters, tags: next });
  };

  const active = filtersActive(filters);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 border-b px-3 py-2 sm:px-6",
        className,
      )}
    >
      <div className="relative w-full max-w-xs flex-shrink-0 sm:w-64">
        <Search
          className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          ref={inputRef}
          type="search"
          value={filters.query}
          onChange={(e) => onChange({ ...filters, query: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              onChange({ ...filters, query: "" });
              (e.currentTarget as HTMLInputElement).blur();
            }
          }}
          placeholder="Search tasks (press /)"
          aria-label="Search tasks"
          className="pl-8"
        />
      </div>

      {availablePriorities.length > 0 && (
        <div
          role="group"
          aria-label="Filter by priority"
          className="flex flex-wrap items-center gap-1"
        >
          <span className="text-xs text-muted-foreground">Priority:</span>
          {availablePriorities.map((p) => {
            const on = filters.priorities.has(p);
            return (
              <button
                key={p}
                type="button"
                onClick={() => togglePriority(p)}
                aria-pressed={on}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-xs transition-colors",
                  on
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-input hover:bg-accent hover:text-accent-foreground",
                )}
              >
                p{p}
              </button>
            );
          })}
        </div>
      )}

      {availableTags.length > 0 && (
        <div
          role="group"
          aria-label="Filter by tag"
          className="flex flex-wrap items-center gap-1"
        >
          <span className="text-xs text-muted-foreground">Tags:</span>
          {availableTags.map((t) => {
            const on = filters.tags.has(t);
            return (
              <button
                key={t}
                type="button"
                onClick={() => toggleTag(t)}
                aria-pressed={on}
                className={cn(
                  "rounded-md border px-2 py-0.5 text-xs transition-colors",
                  on
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-input hover:bg-accent hover:text-accent-foreground",
                )}
              >
                {t}
              </button>
            );
          })}
        </div>
      )}

      <div className="ml-auto flex items-center gap-2">
        {active && (
          <>
            <span
              className="text-xs text-muted-foreground"
              aria-live="polite"
            >
              {matchedCount} / {totalCount}
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => onChange(EMPTY_FILTERS)}
              className="h-7 gap-1 px-2 text-xs"
              title="Clear filters"
            >
              <X className="h-3 w-3" aria-hidden="true" />
              Clear
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
