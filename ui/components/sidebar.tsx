"use client";

import { Folder, LayoutGrid, Plus } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Project } from "@/lib/types";

interface SidebarContentProps {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onNewProject?: () => void;
  loading?: boolean;
  error?: string | null;
  showHeader?: boolean;
}

export function SidebarContent({
  projects,
  selectedId,
  onSelect,
  onNewProject,
  loading,
  error,
  showHeader = true,
}: SidebarContentProps) {
  return (
    <>
      {showHeader && (
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <LayoutGrid className="h-5 w-5" aria-hidden="true" />
          <span className="text-sm font-semibold">agi</span>
        </div>
      )}
      <nav className="flex-1 overflow-y-auto p-2">
        <div className="flex items-center justify-between px-2 py-1">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Projects
          </span>
          {onNewProject && (
            <button
              type="button"
              onClick={onNewProject}
              aria-label="New project"
              title="New project"
              className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
        {loading ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <p className="px-2 py-3 text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : projects.length === 0 ? (
          <div className="px-2 py-3">
            <p className="text-sm text-muted-foreground">No projects yet.</p>
            {onNewProject && (
              <button
                type="button"
                onClick={onNewProject}
                className="mt-2 text-xs font-medium text-primary hover:underline"
              >
                + Create your first project
              </button>
            )}
          </div>
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
    </>
  );
}

interface SidebarProps extends SidebarContentProps {
  className?: string;
}

export function Sidebar({ className, ...contentProps }: SidebarProps) {
  return (
    <aside
      className={cn(
        "hidden h-full w-64 shrink-0 flex-col border-r bg-muted/20 md:flex",
        className,
      )}
    >
      <SidebarContent {...contentProps} />
    </aside>
  );
}
