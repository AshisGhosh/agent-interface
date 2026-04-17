"use client";

import { useCallback, useEffect, useState } from "react";

import { Board } from "@/components/board";
import { NewProjectDialog } from "@/components/new-project-dialog";
import { Sidebar } from "@/components/sidebar";
import { listProjects } from "@/lib/api";
import type { Project } from "@/lib/types";

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newProjectOpen, setNewProjectOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const list = await listProjects();
        if (cancelled) return;
        setProjects(list);
        setSelectedId((cur) => cur ?? list[0]?.id ?? null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleProjectCreated = useCallback((project: Project) => {
    setProjects((prev) =>
      prev.some((p) => p.id === project.id) ? prev : [...prev, project],
    );
    setSelectedId(project.id);
  }, []);

  return (
    <div className="flex h-screen">
      <Sidebar
        projects={projects}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onNewProject={() => setNewProjectOpen(true)}
        loading={loading}
        error={error}
      />
      <Board className="flex-1" projectId={selectedId} />
      <NewProjectDialog
        open={newProjectOpen}
        onOpenChange={setNewProjectOpen}
        onCreated={handleProjectCreated}
      />
    </div>
  );
}
