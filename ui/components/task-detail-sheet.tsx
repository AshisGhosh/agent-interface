"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { listTaskEvents } from "@/lib/api";
import type { Task, TaskEvent } from "@/lib/types";

function priorityVariant(
  p: number,
): "destructive" | "default" | "secondary" | "muted" {
  if (p <= 0) return "destructive";
  if (p === 1) return "default";
  if (p === 2) return "secondary";
  return "muted";
}

interface TaskDetailSheetProps {
  task: Task | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface SSEFrame {
  id: number | null;
  task_id: string;
  event_type: string;
  actor: string;
  payload: string | null;
  created_at: string;
}

export function TaskDetailSheet({
  task,
  open,
  onOpenChange,
}: TaskDetailSheetProps) {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const taskId = task?.id ?? null;

  useEffect(() => {
    if (!open || !taskId) {
      setEvents([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const evts = await listTaskEvents(taskId);
        if (!cancelled) setEvents(evts);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, taskId]);

  useEffect(() => {
    if (!open || !taskId) return;
    const lastSeen = events.reduce(
      (max, e) => (e.id != null && e.id > max ? e.id : max),
      0,
    );
    const es = new EventSource(`/api/events/stream?since_id=${lastSeen}`);
    es.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as SSEFrame;
        if (parsed.task_id !== taskId) return;
        setEvents((prev) => {
          if (parsed.id != null && prev.some((e) => e.id === parsed.id)) {
            return prev;
          }
          return [
            ...prev,
            {
              id: parsed.id,
              task_id: parsed.task_id,
              event_type: parsed.event_type,
              actor: parsed.actor,
              payload_json: parsed.payload,
              created_at: parsed.created_at,
            },
          ];
        });
      } catch {
        // heartbeats / prelude frames — ignore
      }
    };
    return () => {
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, taskId]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full max-w-xl gap-0 p-0 sm:max-w-xl"
      >
        <SheetHeader className="pr-12">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              {taskId ?? ""}
            </span>
            {task && (
              <>
                <Badge variant={priorityVariant(task.priority)}>
                  p{task.priority}
                </Badge>
                <Badge variant="outline" className="capitalize">
                  {task.status.replace("_", " ")}
                </Badge>
              </>
            )}
          </div>
          <SheetTitle className="text-base font-semibold">
            {task?.title ?? "Task detail"}
          </SheetTitle>
          <SheetDescription className="sr-only">
            Details for task {taskId ?? ""}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {error && (
            <div
              className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
              role="alert"
            >
              {error}
            </div>
          )}
          {task && (
            <div className="space-y-5">
              <DescriptionBlock task={task} />
              <MetaGrid task={task} />
              <TagsBlock task={task} />
              <DependenciesBlock task={task} />
              <EventTimeline
                events={events}
                loading={loading && events.length === 0}
              />
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function DescriptionBlock({ task }: { task: Task }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Description
      </h3>
      {task.description ? (
        <pre className="whitespace-pre-wrap rounded-md border bg-muted/40 px-3 py-2 font-sans text-sm">
          {task.description}
        </pre>
      ) : (
        <p className="text-sm italic text-muted-foreground">None.</p>
      )}
    </section>
  );
}

function MetaGrid({ task }: { task: Task }) {
  return (
    <section className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
      <MetaRow label="Created" value={formatTimestamp(task.created_at)} />
      <MetaRow label="Updated" value={formatTimestamp(task.updated_at)} />
      {task.closed_at && (
        <MetaRow label="Closed" value={formatTimestamp(task.closed_at)} />
      )}
      <MetaRow
        label="Assigned"
        value={task.assigned_session_id ?? "(unassigned)"}
        mono
      />
      {task.parent_id && (
        <MetaRow label="Parent" value={task.parent_id} mono />
      )}
      {task.worktree_path && (
        <MetaRow label="Worktree" value={task.worktree_path} mono />
      )}
    </section>
  );
}

function MetaRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className={mono ? "font-mono text-[11px]" : ""}>{value}</span>
    </div>
  );
}

function TagsBlock({ task }: { task: Task }) {
  if (task.tags.length === 0) return null;
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Tags
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {task.tags.map((tag) => (
          <Badge key={tag} variant="outline" className="font-normal">
            {tag}
          </Badge>
        ))}
      </div>
    </section>
  );
}

function DependenciesBlock({ task }: { task: Task }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Dependencies
      </h3>
      {task.depends_on.length === 0 ? (
        <p className="text-xs text-muted-foreground">None.</p>
      ) : (
        <ul className="space-y-1">
          {task.depends_on.map((dep) => (
            <li
              key={dep}
              className="rounded-md border bg-muted/40 px-2 py-1 font-mono text-xs"
            >
              {dep}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const DONE_EVENT_TYPES = new Set(["done", "progress", "blocked", "reopened"]);

function EventTimeline({
  events,
  loading,
}: {
  events: TaskEvent[];
  loading: boolean;
}) {
  const sorted = useMemo(
    () =>
      [...events].sort((a, b) => {
        if (a.id != null && b.id != null) return a.id - b.id;
        return a.created_at.localeCompare(b.created_at);
      }),
    [events],
  );

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Activity log
        </h3>
        <span className="text-xs text-muted-foreground">
          {sorted.length} event{sorted.length === 1 ? "" : "s"}
        </span>
      </div>
      {loading && (
        <p className="text-xs text-muted-foreground">Loading events…</p>
      )}
      {!loading && sorted.length === 0 && (
        <p className="text-xs text-muted-foreground">No events yet.</p>
      )}
      {sorted.length > 0 && (
        <ol className="space-y-2 border-l pl-3">
          {sorted.map((evt, idx) => {
            const highlight = DONE_EVENT_TYPES.has(evt.event_type);
            const note = extractNote(evt);
            return (
              <li
                key={evt.id ?? `${evt.created_at}-${idx}`}
                className={
                  highlight
                    ? "rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs"
                    : "rounded-md bg-muted/30 px-3 py-2 text-xs"
                }
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-foreground">
                    {evt.event_type}
                  </span>
                  <time className="font-mono text-[10px] text-muted-foreground">
                    {formatTimestamp(evt.created_at)}
                  </time>
                </div>
                <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                  {evt.actor}
                </div>
                {note && (
                  <p className="mt-1 whitespace-pre-wrap text-xs text-foreground">
                    {note}
                  </p>
                )}
                {!note && evt.payload_json && (
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-background/60 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                    {formatPayload(evt.payload_json)}
                  </pre>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function extractNote(evt: TaskEvent): string | null {
  if (!evt.payload_json) return null;
  try {
    const obj = JSON.parse(evt.payload_json) as Record<string, unknown>;
    for (const key of ["note", "summary", "reason"]) {
      const val = obj[key];
      if (typeof val === "string" && val.trim() !== "") return val;
    }
  } catch {
    // non-JSON payload — fall through
  }
  return null;
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function formatPayload(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
