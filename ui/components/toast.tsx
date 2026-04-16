"use client";

import { AlertTriangle, X } from "lucide-react";

import { cn } from "@/lib/utils";

export interface ToastMessage {
  id: string;
  title: string;
  body?: string;
  taskId?: string;
}

interface ToastStackProps {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
  className?: string;
}

export function ToastStack({ toasts, onDismiss, className }: ToastStackProps) {
  if (toasts.length === 0) return null;
  return (
    <div
      className={cn(
        "pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2",
        className,
      )}
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="pointer-events-auto flex items-start gap-2 rounded-md border bg-card/95 p-3 text-sm shadow-md backdrop-blur animate-toast-in"
        >
          <AlertTriangle
            className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
            aria-hidden="true"
          />
          <div className="min-w-0 flex-1">
            <div className="font-medium leading-snug">{t.title}</div>
            {t.body && (
              <div className="mt-0.5 text-xs text-muted-foreground">
                {t.body}
              </div>
            )}
            {t.taskId && (
              <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                {t.taskId}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={() => onDismiss(t.id)}
            aria-label="Dismiss"
            className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
