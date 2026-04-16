"use client";

import { forwardRef } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { User } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { Task } from "@/lib/types";

function priorityVariant(p: number): "destructive" | "default" | "secondary" | "muted" {
  if (p <= 0) return "destructive";
  if (p === 1) return "default";
  if (p === 2) return "secondary";
  return "muted";
}

interface TaskCardProps {
  task: Task;
  dragging?: boolean;
  className?: string;
  onClick?: () => void;
}

export const TaskCard = forwardRef<HTMLDivElement, TaskCardProps>(
  function TaskCard({ task, dragging, className, onClick }, ref) {
    return (
      <Card
        ref={ref}
        role={onClick ? "button" : undefined}
        tabIndex={onClick ? 0 : undefined}
        onClick={onClick}
        onKeyDown={
          onClick
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onClick();
                }
              }
            : undefined
        }
        className={cn(
          "select-none space-y-2 p-3 text-sm shadow-sm transition-shadow",
          onClick && "cursor-pointer hover:shadow-md focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          dragging && "opacity-60",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="font-mono text-xs text-muted-foreground">{task.id}</span>
          <Badge variant={priorityVariant(task.priority)} className="shrink-0">
            p{task.priority}
          </Badge>
        </div>
        <div className="font-medium leading-snug text-foreground">
          {task.title}
        </div>
        {(task.tags.length > 0 || task.assigned_session_id) && (
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {task.tags.map((tag) => (
              <Badge key={tag} variant="outline" className="font-normal">
                {tag}
              </Badge>
            ))}
            {task.assigned_session_id && (
              <span
                title={`assigned to ${task.assigned_session_id}`}
                className="ml-auto inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground"
              >
                <User className="h-3 w-3" aria-hidden="true" />
                {task.assigned_session_id.slice(0, 8)}
              </span>
            )}
          </div>
        )}
      </Card>
    );
  },
);

export function SortableTaskCard({
  task,
  onOpen,
}: {
  task: Task;
  onOpen?: (task: Task) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: task.id, data: { type: "task", task } });

  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition,
  };

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <TaskCard
        task={task}
        dragging={isDragging}
        onClick={onOpen ? () => onOpen(task) : undefined}
      />
    </div>
  );
}
