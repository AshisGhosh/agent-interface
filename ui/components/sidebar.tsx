"use client";

import { useState } from "react";
import { Folder, LayoutGrid, Plus } from "lucide-react";

import { NewProjectDialog } from "@/components/new-project-dialog";
import {
  useProjects,
  type ProjectSummary,
} from "@/components/projects-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function summaryLine(summary: ProjectSummary | undefined): string {
  if (!summary || summary.total === 0) return "No tasks";
  const inProgress = summary.byStatus.in_progress ?? 0;
  const review = summary.byStatus.review ?? 0;
  const blocked = summary.byStatus.blocked ?? 0;
  const parts = [`${summary.open}/${summary.total} open`];
  if (inProgress) parts.push(`${inProgress} in progress`);
  if (review) parts.push(`${review} review`);
  if (blocked) parts.push(`${blocked} blocked`);
  return parts.join(" · ");
}

export function Sidebar({ className }: { className?: string }) {
  const {
    projects,
    summaries,
    selectedProjectId,
    selectProject,
    loading,
    error,
  } = useProjects();
  const [dialogOpen, setDialogOpen] = useState(false);

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
      <div className="flex items-center justify-between px-3 pt-3 pb-1">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Projects
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6"
          aria-label="New project"
          onClick={() => setDialogOpen(true)}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {error ? (
          <p
            className="px-2 py-3 text-sm text-destructive"
            role="alert"
          >
            {error}
          </p>
        ) : loading && projects.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">Loading…</p>
        ) : projects.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">
            No projects yet.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {projects.map((p) => {
              const isSelected = p.id === selectedProjectId;
              const summary = summaries[p.id];
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => selectProject(p.id)}
                    aria-current={isSelected ? "true" : undefined}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground",
                      isSelected && "bg-accent text-accent-foreground",
                    )}
                  >
                    <Folder
                      className="mt-0.5 h-4 w-4 shrink-0"
                      aria-hidden="true"
                    />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{p.name}</span>
                      <span className="truncate text-xs text-muted-foreground">
                        {summaryLine(summary)}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>
      <NewProjectDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </aside>
  );
}
