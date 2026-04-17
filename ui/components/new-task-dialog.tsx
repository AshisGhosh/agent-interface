"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { createTask } from "@/lib/api";
import type { Task } from "@/lib/types";

interface NewTaskDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string | null;
  existingTasks: Task[];
  onCreated?: (task: Task) => void;
}

const PRIORITY_OPTIONS = [
  { value: 0, label: "p0 — urgent" },
  { value: 1, label: "p1 — high" },
  { value: 2, label: "p2 — normal" },
  { value: 3, label: "p3 — low" },
];

function parseTags(raw: string): string[] {
  return raw
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

export function NewTaskDialog({
  open,
  onOpenChange,
  projectId,
  existingTasks,
  onCreated,
}: NewTaskDialogProps) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState(2);
  const [tags, setTags] = useState("");
  const [dependsOn, setDependsOn] = useState<Set<string>>(new Set());
  const [depsQuery, setDepsQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setTitle("");
      setDescription("");
      setPriority(2);
      setTags("");
      setDependsOn(new Set());
      setDepsQuery("");
      setError(null);
      // Autofocus the title field once the dialog mounts.
      setTimeout(() => titleRef.current?.focus(), 0);
    }
  }, [open]);

  const filteredTasks = useMemo(() => {
    const q = depsQuery.trim().toLowerCase();
    const open = existingTasks.filter((t) => t.status !== "done");
    if (!q) return open.slice(0, 50);
    return open
      .filter(
        (t) =>
          t.id.toLowerCase().includes(q) ||
          t.title.toLowerCase().includes(q),
      )
      .slice(0, 50);
  }, [existingTasks, depsQuery]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId) {
      setError("No project selected.");
      return;
    }
    if (!title.trim()) {
      setError("Title is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const task = await createTask({
        project: projectId,
        title: title.trim(),
        description: description.trim() || null,
        priority,
        tags: parseTags(tags),
        depends_on: Array.from(dependsOn),
      });
      onCreated?.(task);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  function toggleDep(id: string) {
    setDependsOn((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>New task</DialogTitle>
          <DialogDescription>
            Create a task in the current project.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="new-task-title">Title</Label>
            <Input
              id="new-task-title"
              ref={titleRef}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Short, actionable summary"
              required
              maxLength={200}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-task-desc">Description</Label>
            <Textarea
              id="new-task-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional context, acceptance criteria, links…"
              rows={4}
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="new-task-priority">Priority</Label>
              <select
                id="new-task-priority"
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {PRIORITY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-task-tags">Tags</Label>
              <Input
                id="new-task-tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="frontend, bug (comma-separated)"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Depends on</Label>
              <span className="text-xs text-muted-foreground">
                {dependsOn.size} selected
              </span>
            </div>
            <Input
              value={depsQuery}
              onChange={(e) => setDepsQuery(e.target.value)}
              placeholder="Filter tasks by id or title…"
            />
            <ScrollArea className="h-40 rounded-md border">
              <ul className="divide-y">
                {filteredTasks.length === 0 ? (
                  <li className="px-3 py-4 text-center text-xs text-muted-foreground">
                    No matching tasks.
                  </li>
                ) : (
                  filteredTasks.map((t) => {
                    const checked = dependsOn.has(t.id);
                    return (
                      <li key={t.id}>
                        <label className="flex cursor-pointer items-start gap-2 px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground">
                          <Checkbox
                            checked={checked}
                            onCheckedChange={() => toggleDep(t.id)}
                            className="mt-0.5"
                            aria-label={`depends on ${t.id}`}
                          />
                          <span className="flex min-w-0 flex-col">
                            <span className="font-mono text-[11px] text-muted-foreground">
                              {t.id} · {t.status}
                            </span>
                            <span className="truncate">{t.title}</span>
                          </span>
                        </label>
                      </li>
                    );
                  })
                )}
              </ul>
            </ScrollArea>
          </div>
          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !title.trim()}>
              {submitting ? "Creating…" : "Create task"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
