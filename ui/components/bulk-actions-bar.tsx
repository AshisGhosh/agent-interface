"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { patchTask } from "@/lib/api";
import type { Task, TaskStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS: { value: TaskStatus; label: string }[] = [
  { value: "in_progress", label: "In progress" },
  { value: "review", label: "Review" },
  { value: "blocked", label: "Blocked" },
  { value: "ready", label: "Ready" },
  { value: "backlog", label: "Backlog" },
  { value: "done", label: "Done" },
];

const PRIORITY_OPTIONS = [0, 1, 2, 3];

interface BulkActionsBarProps {
  selected: Task[];
  onClear: () => void;
  onApplied: () => Promise<void> | void;
  className?: string;
}

export function BulkActionsBar({
  selected,
  onClear,
  onApplied,
  className,
}: BulkActionsBarProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const count = selected.length;
  const allBacklog = count > 0 && selected.every((t) => t.status === "backlog");

  async function applyPatches(patches: { id: string; body: Parameters<typeof patchTask>[1] }[]) {
    setBusy(true);
    setError(null);
    try {
      await Promise.all(patches.map((p) => patchTask(p.id, p.body)));
      await onApplied();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: TaskStatus) {
    const patches = selected
      .filter((t) => t.status !== status)
      .map((t) => ({ id: t.id, body: { status } }));
    if (patches.length === 0) return;
    await applyPatches(patches);
  }

  async function setPriority(priority: number) {
    const patches = selected
      .filter((t) => t.priority !== priority)
      .map((t) => ({ id: t.id, body: { priority } }));
    if (patches.length === 0) return;
    await applyPatches(patches);
  }

  async function promoteAll() {
    const patches = selected
      .filter((t) => t.status === "backlog")
      .map((t) => ({ id: t.id, body: { status: "ready" as TaskStatus } }));
    if (patches.length === 0) return;
    await applyPatches(patches);
  }

  if (count === 0) return null;

  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className={cn(
        "sticky bottom-4 mx-auto flex max-w-fit flex-wrap items-center gap-3 rounded-lg border bg-background px-4 py-2 text-sm shadow-lg",
        className,
      )}
    >
      <span className="font-medium">{count} selected</span>

      <div className="flex items-center gap-1.5">
        <label htmlFor="bulk-status" className="text-xs text-muted-foreground">
          Status
        </label>
        <select
          id="bulk-status"
          defaultValue=""
          disabled={busy}
          onChange={(e) => {
            const v = e.target.value as TaskStatus | "";
            e.currentTarget.value = "";
            if (v) void setStatus(v);
          }}
          className="h-8 rounded-md border border-input bg-transparent px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="" disabled>
            Set status…
          </option>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-1.5">
        <label htmlFor="bulk-priority" className="text-xs text-muted-foreground">
          Priority
        </label>
        <select
          id="bulk-priority"
          defaultValue=""
          disabled={busy}
          onChange={(e) => {
            const v = e.target.value;
            e.currentTarget.value = "";
            if (v !== "") void setPriority(Number(v));
          }}
          className="h-8 rounded-md border border-input bg-transparent px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="" disabled>
            Set priority…
          </option>
          {PRIORITY_OPTIONS.map((p) => (
            <option key={p} value={p}>
              p{p}
            </option>
          ))}
        </select>
      </div>

      <Button
        type="button"
        size="sm"
        variant="secondary"
        disabled={busy || !allBacklog}
        onClick={() => void promoteAll()}
        title={allBacklog ? "Promote backlog to ready" : "Select only backlog tasks to promote"}
      >
        Promote to ready
      </Button>

      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={busy}
        onClick={onClear}
      >
        Clear
      </Button>

      {error && (
        <span className="text-xs text-destructive" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
