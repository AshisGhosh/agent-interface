"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { getTask, listTaskEvents, patchTask } from "@/lib/api";
import type {
  SSETaskEvent,
  Task,
  TaskEvent,
  TaskPatch,
  TaskStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS: { value: TaskStatus; label: string }[] = [
  { value: "backlog", label: "Backlog" },
  { value: "ready", label: "Ready" },
  { value: "in_progress", label: "In progress" },
  { value: "review", label: "Review" },
  { value: "blocked", label: "Blocked" },
  { value: "done", label: "Done" },
];

const PRIORITY_OPTIONS = [
  { value: 0, label: "p0 — urgent" },
  { value: 1, label: "p1 — high" },
  { value: 2, label: "p2 — normal" },
  { value: 3, label: "p3 — low" },
];

function priorityVariant(
  p: number,
): "destructive" | "default" | "secondary" | "muted" {
  if (p <= 0) return "destructive";
  if (p === 1) return "default";
  if (p === 2) return "secondary";
  return "muted";
}

interface TaskDetailSheetProps {
  taskId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTaskChanged?: (task: Task) => void;
}

export function TaskDetailSheet({
  taskId,
  open,
  onOpenChange,
  onTaskChanged,
}: TaskDetailSheetProps) {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load task + events when the sheet opens for a given task id.
  useEffect(() => {
    if (!open || !taskId) {
      setTask(null);
      setEvents([]);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const [t, evts] = await Promise.all([
          getTask(taskId),
          listTaskEvents(taskId),
        ]);
        if (cancelled) return;
        setTask(t);
        setEvents(evts);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, taskId]);

  // SSE subscription — filter events for this task, append new ones.
  useEffect(() => {
    if (!open || !taskId) return;
    // Start after the latest event we already loaded (or 0).
    const lastSeen = events.reduce(
      (max, e) => (e.id != null && e.id > max ? e.id : max),
      0,
    );
    const url = `/api/events/stream?since_id=${lastSeen}`;
    const es = new EventSource(url);

    es.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(msg.data) as SSETaskEvent;
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

        // Refresh task snapshot when a field/status event lands, so the
        // edit controls reflect server-side state.
        if (
          ["updated", "status_changed", "claimed", "blocked", "unblocked",
            "done", "ready", "progress", "reopened"].includes(parsed.event_type)
        ) {
          (async () => {
            try {
              const fresh = await getTask(taskId);
              setTask(fresh);
            } catch {
              // ignore — the user will see the stale snapshot but can close
              // and reopen to force a refetch.
            }
          })();
        }
      } catch {
        // Heartbeats and the connection prelude aren't JSON frames — ignore.
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects; we surface nothing to the user.
    };

    return () => {
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, taskId]);

  const applyPatch = useCallback(
    async (patch: TaskPatch) => {
      if (!taskId) return;
      setSaving(true);
      setError(null);
      try {
        const updated = await patchTask(taskId, patch);
        setTask(updated);
        onTaskChanged?.(updated);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSaving(false);
      }
    },
    [taskId, onTaskChanged],
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full max-w-xl gap-0 p-0 sm:max-w-xl"
      >
        <SheetHeader className="pr-12">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              {taskId ?? ""}
            </span>
            {task && (
              <Badge variant={priorityVariant(task.priority)}>
                p{task.priority}
              </Badge>
            )}
            {task && (
              <Badge variant="outline" className="capitalize">
                {task.status.replace("_", " ")}
              </Badge>
            )}
            {saving && (
              <span className="text-xs text-muted-foreground">Saving…</span>
            )}
          </div>
          <SheetTitle className="sr-only">Task detail</SheetTitle>
          <SheetDescription className="sr-only">
            View and edit task {taskId ?? ""}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading && !task && (
            <div className="py-8 text-center text-sm text-muted-foreground">
              Loading…
            </div>
          )}
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
              <TitleField task={task} onSave={applyPatch} />
              <DescriptionField task={task} onSave={applyPatch} />
              <div className="grid grid-cols-2 gap-4">
                <StatusField task={task} onSave={applyPatch} />
                <PriorityField task={task} onSave={applyPatch} />
              </div>
              <TagsField task={task} onSave={applyPatch} />
              <AssigneeField task={task} onSave={applyPatch} />
              <DependenciesField task={task} />
              <EventTimeline events={events} />
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ── field editors ──────────────────────────────────────────────────────────

function TitleField({
  task,
  onSave,
}: {
  task: Task;
  onSave: (patch: TaskPatch) => void | Promise<void>;
}) {
  const [value, setValue] = useState(task.title);

  useEffect(() => {
    setValue(task.title);
  }, [task.id, task.title]);

  const commit = () => {
    const next = value.trim();
    if (!next || next === task.title) {
      setValue(task.title);
      return;
    }
    void onSave({ title: next });
  };

  return (
    <div className="space-y-1.5">
      <Label htmlFor="task-title">Title</Label>
      <Input
        id="task-title"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            e.currentTarget.blur();
          } else if (e.key === "Escape") {
            setValue(task.title);
            e.currentTarget.blur();
          }
        }}
        className="h-10 text-base font-semibold"
      />
    </div>
  );
}

function DescriptionField({
  task,
  onSave,
}: {
  task: Task;
  onSave: (patch: TaskPatch) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(task.description ?? "");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setValue(task.description ?? "");
  }, [task.id, task.description]);

  useEffect(() => {
    if (editing) textareaRef.current?.focus();
  }, [editing]);

  const commit = () => {
    setEditing(false);
    const original = task.description ?? "";
    if (value === original) return;
    if (value.trim() === "") {
      void onSave({ clear_description: true });
    } else {
      void onSave({ description: value });
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label>Description</Label>
        {!editing && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-xs font-normal text-muted-foreground hover:text-foreground"
          >
            Edit
          </button>
        )}
      </div>
      {editing ? (
        <Textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setValue(task.description ?? "");
              setEditing(false);
            } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              e.currentTarget.blur();
            }
          }}
          rows={8}
          placeholder="Add a description (Markdown supported). Cmd/Ctrl+Enter to save, Esc to cancel."
          className="font-mono text-sm"
        />
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className={cn(
            "block w-full rounded-md border border-transparent bg-muted/40 px-3 py-2 text-left text-sm hover:border-input",
            !task.description && "text-muted-foreground italic",
          )}
        >
          {task.description ? (
            <div className="prose prose-sm max-w-none dark:prose-invert [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_pre]:my-2 [&_code]:text-xs">
              <ReactMarkdown>{task.description}</ReactMarkdown>
            </div>
          ) : (
            "Click to add a description…"
          )}
        </button>
      )}
    </div>
  );
}

function StatusField({
  task,
  onSave,
}: {
  task: Task;
  onSave: (patch: TaskPatch) => void | Promise<void>;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor="task-status">Status</Label>
      <select
        id="task-status"
        value={task.status}
        onChange={(e) => {
          const next = e.target.value as TaskStatus;
          if (next === task.status) return;
          const patch: TaskPatch = { status: next };
          if (next === "blocked") {
            const reason = window.prompt(
              "Why is this blocked?",
              "blocked via UI",
            );
            if (reason === null) return;
            patch.block_reason = reason || "blocked via UI";
            patch.block_needs = "user";
          } else if (next === "done") {
            const summary = window.prompt("Summary for done:", "closed via UI");
            if (summary === null) return;
            patch.done_summary = summary || "closed via UI";
          }
          void onSave(patch);
        }}
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {STATUS_OPTIONS.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function PriorityField({
  task,
  onSave,
}: {
  task: Task;
  onSave: (patch: TaskPatch) => void | Promise<void>;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor="task-priority">Priority</Label>
      <select
        id="task-priority"
        value={task.priority}
        onChange={(e) => {
          const next = Number(e.target.value);
          if (next === task.priority) return;
          void onSave({ priority: next });
        }}
        className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {PRIORITY_OPTIONS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function TagsField({
  task,
  onSave,
}: {
  task: Task;
  onSave: (patch: TaskPatch) => void | Promise<void>;
}) {
  const [draft, setDraft] = useState("");

  const addTag = (raw: string) => {
    const tag = raw.trim();
    if (!tag) return;
    if (task.tags.includes(tag)) {
      setDraft("");
      return;
    }
    void onSave({ tags: [...task.tags, tag] });
    setDraft("");
  };

  const removeTag = (tag: string) => {
    void onSave({ tags: task.tags.filter((t) => t !== tag) });
  };

  return (
    <div className="space-y-1.5">
      <Label>Tags</Label>
      <div className="flex flex-wrap items-center gap-1.5">
        {task.tags.map((tag) => (
          <Badge
            key={tag}
            variant="outline"
            className="gap-1 font-normal"
          >
            {tag}
            <button
              type="button"
              aria-label={`Remove tag ${tag}`}
              onClick={() => removeTag(tag)}
              className="-mr-1 rounded-sm text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => addTag(draft)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addTag(draft);
            } else if (e.key === "Backspace" && draft === "" && task.tags.length > 0) {
              removeTag(task.tags[task.tags.length - 1]);
            }
          }}
          placeholder="Add tag…"
          className="h-7 w-28 text-xs"
        />
      </div>
    </div>
  );
}

function AssigneeField({
  task,
  onSave,
}: {
  task: Task;
  onSave: (patch: TaskPatch) => void | Promise<void>;
}) {
  const [value, setValue] = useState(task.assigned_session_id ?? "");

  useEffect(() => {
    setValue(task.assigned_session_id ?? "");
  }, [task.id, task.assigned_session_id]);

  const commit = () => {
    const next = value.trim();
    const current = task.assigned_session_id ?? "";
    if (next === current) return;
    if (next === "") {
      void onSave({ clear_assignment: true });
    } else {
      void onSave({ assigned_session_id: next });
    }
  };

  return (
    <div className="space-y-1.5">
      <Label htmlFor="task-assignee">Assigned session</Label>
      <div className="flex gap-2">
        <Input
          id="task-assignee"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            } else if (e.key === "Escape") {
              setValue(task.assigned_session_id ?? "");
              e.currentTarget.blur();
            }
          }}
          placeholder="(unassigned)"
          className="font-mono text-xs"
        />
        {task.assigned_session_id && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setValue("");
              void onSave({ clear_assignment: true });
            }}
          >
            Clear
          </Button>
        )}
      </div>
    </div>
  );
}

function DependenciesField({ task }: { task: Task }) {
  if (task.depends_on.length === 0) {
    return (
      <div className="space-y-1.5">
        <Label>Dependencies</Label>
        <p className="text-xs text-muted-foreground">None.</p>
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      <Label>Dependencies</Label>
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
    </div>
  );
}

// ── event timeline ─────────────────────────────────────────────────────────

function EventTimeline({ events }: { events: TaskEvent[] }) {
  const sorted = useMemo(
    () =>
      [...events].sort((a, b) => {
        if (a.id != null && b.id != null) return a.id - b.id;
        return a.created_at.localeCompare(b.created_at);
      }),
    [events],
  );

  return (
    <div className="space-y-2 pt-2">
      <div className="flex items-center justify-between">
        <Label>Timeline</Label>
        <span className="text-xs text-muted-foreground">
          {sorted.length} event{sorted.length === 1 ? "" : "s"}
        </span>
      </div>
      <ol className="space-y-2 border-l pl-3">
        {sorted.length === 0 && (
          <li className="text-xs text-muted-foreground">No events yet.</li>
        )}
        {sorted.map((evt, idx) => (
          <li
            key={evt.id ?? `${evt.created_at}-${idx}`}
            className="relative rounded-md bg-muted/30 px-3 py-2 text-xs"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-foreground">
                {evt.event_type}
              </span>
              <time className="font-mono text-[10px] text-muted-foreground">
                {formatTimestamp(evt.created_at)}
              </time>
            </div>
            <div className="mt-1 flex items-center gap-2 text-muted-foreground">
              <span className="font-mono text-[10px]">{evt.actor}</span>
            </div>
            {evt.payload_json && (
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words rounded bg-background/60 px-2 py-1 font-mono text-[10px] text-muted-foreground">
                {formatPayload(evt.payload_json)}
              </pre>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
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
