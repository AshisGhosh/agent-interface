"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useProjects } from "@/components/projects-provider";
import { cn } from "@/lib/utils";

const COLUMNS = [
  { key: "backlog", label: "Backlog" },
  { key: "ready", label: "Ready" },
  { key: "in_progress", label: "In progress" },
  { key: "review", label: "Review" },
  { key: "done", label: "Done" },
] as const;

export function Board({ className }: { className?: string }) {
  const { projects, selectedProjectId } = useProjects();
  const selected = projects.find((p) => p.id === selectedProjectId) ?? null;

  return (
    <section className={cn("flex h-full flex-col", className)}>
      <header className="flex h-14 items-center border-b px-6">
        <h1 className="text-sm font-semibold">
          {selected ? selected.name : "Board"}
        </h1>
      </header>
      <div className="flex flex-1 gap-4 overflow-x-auto p-6">
        {COLUMNS.map((col) => (
          <Card key={col.key} className="flex w-72 shrink-0 flex-col">
            <CardHeader>
              <CardTitle>{col.label}</CardTitle>
            </CardHeader>
            <CardContent className="flex-1 text-sm text-muted-foreground">
              {selected ? "No tasks." : "Select a project."}
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  );
}
