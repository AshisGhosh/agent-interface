"use client";

import { Folder, LayoutGrid } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Project } from "@/lib/types";

interface SidebarProps {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading?: boolean;
  error?: string | null;
  className?: string;
}

export function Sidebar({
  projects,
  selectedId,
  onSelect,
  loading,
  error,
  className,
}: SidebarProps) {
  return (
    <aside
      className={cn(
        "flex h-full w-64 shrink-0 flex-col border-r bg-muted/20",
        className,
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <LayoutGrid className="h-5 w-5" aria-hidden="true" />
        <span className="text-sm font-semibold">agi</span>
      </div>
      <nav className="flex-1 overflow-y-auto p-2">
        <div className="px-2 py-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Projects
        </div>
        {loading ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <p className="px-2 py-3 text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : projects.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">
            No projects yet.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {projects.map((p) => {
              const active = p.id === selectedId;
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(p.id)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground",
                      active && "bg-accent text-accent-foreground",
                    )}
                  >
                    <Folder className="h-4 w-4" aria-hidden="true" />
                    <span className="truncate">{p.name}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>
    </aside>
  );
}
