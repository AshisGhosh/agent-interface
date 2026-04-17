"use client";

import { useDroppable } from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import { ScrollArea } from "@/components/ui/scroll-area";
import { SortableTaskCard } from "@/components/task-card";
import { cn } from "@/lib/utils";
import type { Task, TaskStatus } from "@/lib/types";

export const COLUMN_ID_PREFIX = "col:";

export interface ColumnDef {
  key: TaskStatus;
  label: string;
  tone: string;
}

export const COLUMNS: ColumnDef[] = [
  { key: "in_progress", label: "In progress", tone: "text-emerald-600" },
  { key: "review", label: "Review", tone: "text-amber-600" },
  { key: "blocked", label: "Blocked", tone: "text-rose-600" },
  { key: "ready", label: "Ready", tone: "text-sky-600" },
  { key: "backlog", label: "Backlog", tone: "text-muted-foreground" },
  { key: "done", label: "Done", tone: "text-muted-foreground/60" },
];

interface BoardColumnProps {
  column: ColumnDef;
  tasks: Task[];
  onOpenTask?: (task: Task) => void;
}

export function BoardColumn({ column, tasks, onOpenTask }: BoardColumnProps) {
  const droppableId = `${COLUMN_ID_PREFIX}${column.key}`;
  const { setNodeRef, isOver } = useDroppable({
    id: droppableId,
    data: { type: "column", status: column.key },
  });
  const taskIds = tasks.map((t) => t.id);

  return (
    <div
      className={cn(
        "flex w-[17rem] shrink-0 flex-col rounded-lg border bg-card sm:w-72",
        isOver && "ring-2 ring-ring/60",
      )}
    >
      <header className="flex items-center justify-between border-b px-3 py-2">
        <span className={cn("text-sm font-semibold", column.tone)}>
          {column.label}
        </span>
        <span className="text-xs text-muted-foreground">{tasks.length}</span>
      </header>
      <ScrollArea className="flex-1">
        <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
          <div ref={setNodeRef} className="flex min-h-[120px] flex-col gap-2 p-2">
            {tasks.length === 0 ? (
              <p className="px-1 py-6 text-center text-xs text-muted-foreground">
                Drop tasks here
              </p>
            ) : (
              tasks.map((t) => (
                <SortableTaskCard key={t.id} task={t} onOpen={onOpenTask} />
              ))
            )}
          </div>
        </SortableContext>
      </ScrollArea>
    </div>
  );
}
