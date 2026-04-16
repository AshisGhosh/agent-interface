import { Folder, LayoutGrid } from "lucide-react";

import { cn } from "@/lib/utils";

type Project = { id: string; name: string };

// Placeholder — real data comes from the FastAPI /projects endpoint once wired.
const projects: Project[] = [];

export function Sidebar({ className }: { className?: string }) {
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
      <nav className="flex-1 overflow-y-auto p-2">
        <div className="px-2 py-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Projects
        </div>
        {projects.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">
            No projects yet.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {projects.map((p) => (
              <li key={p.id}>
                <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground">
                  <Folder className="h-4 w-4" aria-hidden="true" />
                  <span className="truncate">{p.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
    </aside>
  );
}
