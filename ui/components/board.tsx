"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  DragEndEvent,
  DragOverEvent,
  DragOverlay,
  DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import { Menu } from "lucide-react";

import { deleteTask, dispatchAgents, listProjectTasks, patchTask } from "@/lib/api";
import type { Task, TaskStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

import {
  BoardColumn,
  COLUMN_ID_PREFIX,
  COLUMNS,
} from "@/components/board-column";
import { NewTaskDialog } from "@/components/new-task-dialog";
import { TaskCard } from "@/components/task-card";
import { TaskDetailSheet } from "@/components/task-detail-sheet";
import { Button } from "@/components/ui/button";

type TasksByStatus = Record<TaskStatus, Task[]>;

const EMPTY_BY_STATUS: TasksByStatus = {
  in_progress: [],
  review: [],
  blocked: [],
  ready: [],
  backlog: [],
  done: [],
};

function groupByStatus(tasks: Task[]): TasksByStatus {
  const out: TasksByStatus = {
    in_progress: [],
    review: [],
    blocked: [],
    ready: [],
    backlog: [],
    done: [],
  };
  for (const t of tasks) {
    (out[t.status] ??= []).push(t);
  }
  // Sort each column by priority ASC, then created_at ASC — matches the
  // backend's list ordering so the initial render is stable.
  for (const key of Object.keys(out) as TaskStatus[]) {
    out[key].sort(
      (a, b) =>
        a.priority - b.priority || a.created_at.localeCompare(b.created_at),
    );
  }
  return out;
}

function resolveContainer(
  id: string | null,
  tasksByStatus: TasksByStatus,
): TaskStatus | null {
  if (!id) return null;
  if (id.startsWith(COLUMN_ID_PREFIX)) {
    return id.slice(COLUMN_ID_PREFIX.length) as TaskStatus;
  }
  for (const status of Object.keys(tasksByStatus) as TaskStatus[]) {
    if (tasksByStatus[status].some((t) => t.id === id)) return status;
  }
  return null;
}

export interface BoardProps {
  projectId: string | null;
  className?: string;
  onOpenMobileNav?: () => void;
}

export function Board({ projectId, className, onOpenMobileNav }: BoardProps) {
  const [tasksByStatus, setTasksByStatus] =
    useState<TasksByStatus>(EMPTY_BY_STATUS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const [dispatching, setDispatching] = useState(false);

  // Snapshot of the per-status arrays when drag started, so we can diff
  // priorities on drop and only PATCH tasks that actually moved.
  const dragStartSnapshot = useRef<TasksByStatus | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const refresh = useCallback(async () => {
    if (!projectId) {
      setTasksByStatus(EMPTY_BY_STATUS);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const tasks = await listProjectTasks(projectId, { includeClosed: true });
      setTasksByStatus(groupByStatus(tasks));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // "n" shortcut for new task
  useEffect(() => {
    if (!projectId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "n" && e.key !== "N") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      e.preventDefault();
      setNewTaskOpen(true);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [projectId]);

  const readyCount = tasksByStatus.ready?.length ?? 0;

  const onDispatch = useCallback(async () => {
    if (!projectId || readyCount === 0) return;
    const input = prompt(
      `Dispatch how many agents? (${readyCount} ready)`,
      String(Math.min(readyCount, 4)),
    );
    if (!input) return;
    const n = parseInt(input, 10);
    if (isNaN(n) || n < 1) return;
    setDispatching(true);
    try {
      const result = await dispatchAgents(projectId, n);
      alert(`Dispatched ${result.dispatched} agent(s).`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDispatching(false);
    }
  }, [projectId, readyCount, refresh]);

  const onDeleteTask = useCallback(
    async (taskId: string) => {
      if (!confirm(`Delete task ${taskId}?`)) return;
      try {
        await deleteTask(taskId);
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [refresh],
  );

  const allTasks = useMemo(() => {
    const out: Task[] = [];
    for (const key of Object.keys(tasksByStatus) as TaskStatus[]) {
      out.push(...tasksByStatus[key]);
    }
    return out;
  }, [tasksByStatus]);

  const activeTask = useMemo(() => {
    if (!activeId) return null;
    for (const status of Object.keys(tasksByStatus) as TaskStatus[]) {
      const hit = tasksByStatus[status].find((t) => t.id === activeId);
      if (hit) return hit;
    }
    return null;
  }, [activeId, tasksByStatus]);

  const openTask = useMemo(() => {
    if (!openTaskId) return null;
    for (const status of Object.keys(tasksByStatus) as TaskStatus[]) {
      const hit = tasksByStatus[status].find((t) => t.id === openTaskId);
      if (hit) return hit;
    }
    return null;
  }, [openTaskId, tasksByStatus]);

  const onDragStart = useCallback(
    (event: DragStartEvent) => {
      setActiveId(String(event.active.id));
      dragStartSnapshot.current = {
        in_progress: [...tasksByStatus.in_progress],
        review: [...tasksByStatus.review],
        blocked: [...tasksByStatus.blocked],
        ready: [...tasksByStatus.ready],
        backlog: [...tasksByStatus.backlog],
        done: [...tasksByStatus.done],
      };
    },
    [tasksByStatus],
  );

  const onDragOver = useCallback(
    (event: DragOverEvent) => {
      const { active, over } = event;
      if (!over) return;
      const activeIdStr = String(active.id);
      const overIdStr = String(over.id);
      if (activeIdStr === overIdStr) return;

      setTasksByStatus((prev) => {
        const activeContainer = resolveContainer(activeIdStr, prev);
        const overContainer = resolveContainer(overIdStr, prev);
        if (!activeContainer || !overContainer) return prev;
        if (activeContainer === overContainer) return prev;
        if (overContainer === "done") return prev;

        const activeItems = prev[activeContainer];
        const overItems = prev[overContainer];
        const activeIndex = activeItems.findIndex((t) => t.id === activeIdStr);
        if (activeIndex === -1) return prev;

        const moving = activeItems[activeIndex];
        const overIndex = overIdStr.startsWith(COLUMN_ID_PREFIX)
          ? overItems.length
          : overItems.findIndex((t) => t.id === overIdStr);
        const insertAt = overIndex === -1 ? overItems.length : overIndex;

        const next: TasksByStatus = { ...prev };
        next[activeContainer] = [
          ...activeItems.slice(0, activeIndex),
          ...activeItems.slice(activeIndex + 1),
        ];
        const withStatus: Task = { ...moving, status: overContainer };
        next[overContainer] = [
          ...overItems.slice(0, insertAt),
          withStatus,
          ...overItems.slice(insertAt),
        ];
        return next;
      });
    },
    [],
  );

  const onDragEnd = useCallback(
    async (event: DragEndEvent) => {
      const { active, over } = event;
      const snapshot = dragStartSnapshot.current;
      dragStartSnapshot.current = null;
      setActiveId(null);
      if (!over || !snapshot) return;

      const activeIdStr = String(active.id);
      const overIdStr = String(over.id);

      // Commit the final within-column order using arrayMove. We use a
      // functional setState and capture the result via a ref so the async
      // PATCH work below sees the committed state (not a stale closure).
      const committedRef: { value: TasksByStatus | null } = { value: null };
      setTasksByStatus((prev) => {
        const activeContainer = resolveContainer(activeIdStr, prev);
        const overContainer = resolveContainer(overIdStr, prev);
        if (!activeContainer || !overContainer) {
          committedRef.value = prev;
          return prev;
        }
        if (activeContainer !== overContainer) {
          committedRef.value = prev;
          return prev;
        }
        const items = prev[activeContainer];
        const oldIndex = items.findIndex((t) => t.id === activeIdStr);
        const newIndex = overIdStr.startsWith(COLUMN_ID_PREFIX)
          ? items.length - 1
          : items.findIndex((t) => t.id === overIdStr);
        if (oldIndex === -1 || newIndex === -1 || oldIndex === newIndex) {
          committedRef.value = prev;
          return prev;
        }
        const next: TasksByStatus = { ...prev };
        next[activeContainer] = arrayMove(items, oldIndex, newIndex);
        committedRef.value = next;
        return next;
      });

      const committed = committedRef.value;
      if (!committed) return;

      // Figure out what changed vs. the pre-drag snapshot and PATCH it.
      try {
        const originalStatus = (Object.keys(snapshot) as TaskStatus[]).find(
          (s) => snapshot[s].some((t) => t.id === activeIdStr),
        );
        const finalStatus = (Object.keys(committed) as TaskStatus[]).find(
          (s) => committed[s].some((t) => t.id === activeIdStr),
        );
        if (!originalStatus || !finalStatus) return;

        const patches: Promise<unknown>[] = [];

        if (originalStatus !== finalStatus) {
          patches.push(patchTask(activeIdStr, { status: finalStatus }));
        }

        // Reassign priorities in the destination column to reflect the new
        // order. Priorities become dense 0..N-1; only PATCH tasks whose
        // priority actually changed.
        const destColumn: Task[] = committed[finalStatus];
        destColumn.forEach((task, idx) => {
          if (task.priority !== idx) {
            patches.push(patchTask(task.id, { priority: idx }));
          }
        });

        if (patches.length === 0) return;
        await Promise.all(patches);
        // Refresh from the server so priorities/status reflect any derived
        // fields (closed_at, updated_at, transitions that mutate state).
        await refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        // Roll back optimistic state on failure.
        setTasksByStatus(snapshot);
      }
    },
    [refresh],
  );

  const onDragCancel = useCallback(() => {
    const snapshot = dragStartSnapshot.current;
    dragStartSnapshot.current = null;
    setActiveId(null);
    if (snapshot) setTasksByStatus(snapshot);
  }, []);

  return (
    <section className={cn("flex h-full min-w-0 flex-col", className)}>
      <header className="flex h-14 items-center justify-between gap-2 border-b px-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          {onOpenMobileNav && (
            <button
              type="button"
              onClick={onOpenMobileNav}
              aria-label="Open navigation"
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring md:hidden"
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </button>
          )}
          <h1 className="text-sm font-semibold">Board</h1>
          {loading && (
            <span className="hidden text-xs text-muted-foreground sm:inline">
              Loading…
            </span>
          )}
          {error && (
            <span
              className="truncate text-xs text-destructive"
              role="alert"
            >
              {error}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {projectId && (
            <span className="hidden font-mono text-xs text-muted-foreground lg:inline">
              {projectId}
            </span>
          )}
          {projectId && (
            <>
              <Button
                size="sm"
                onClick={() => setNewTaskOpen(true)}
                title="Add task (n)"
              >
                <span className="hidden sm:inline">+ New task</span>
                <span className="sm:hidden">+ New</span>
              </Button>
              {readyCount > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onDispatch}
                  disabled={dispatching}
                  title={`Dispatch agents on ${readyCount} ready task(s)`}
                >
                  {dispatching ? (
                    "Dispatching…"
                  ) : (
                    <>
                      <span className="hidden sm:inline">
                        Dispatch ({readyCount} ready)
                      </span>
                      <span className="sm:hidden">
                        Dispatch ({readyCount})
                      </span>
                    </>
                  )}
                </Button>
              )}
            </>
          )}
        </div>
      </header>
      {!projectId ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Select a project to view its board.
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragOver={onDragOver}
          onDragEnd={onDragEnd}
          onDragCancel={onDragCancel}
        >
          <div className="flex flex-1 gap-3 overflow-x-auto overflow-y-hidden p-3 sm:gap-4 sm:p-6">
            {COLUMNS.map((col) => (
              <BoardColumn
                key={col.key}
                column={col}
                tasks={tasksByStatus[col.key] ?? []}
                onOpenTask={(t) => setOpenTaskId(t.id)}
              />
            ))}
          </div>
          <DragOverlay>
            {activeTask ? <TaskCard task={activeTask} /> : null}
          </DragOverlay>
        </DndContext>
      )}
      <TaskDetailSheet
        task={openTask}
        open={openTaskId !== null}
        onOpenChange={(o) => {
          if (!o) setOpenTaskId(null);
        }}
      />
      <NewTaskDialog
        open={newTaskOpen}
        onOpenChange={setNewTaskOpen}
        projectId={projectId}
        existingTasks={allTasks}
        onCreated={() => void refresh()}
      />
    </section>
  );
}
