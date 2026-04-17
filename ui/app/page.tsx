"use client";

import { useCallback, useEffect, useState } from "react";

import { Board } from "@/components/board";
import { HelpModal, useHelpHotkey } from "@/components/help-modal";
import { Sidebar } from "@/components/sidebar";
import { listProjects } from "@/lib/api";
import type { Project } from "@/lib/types";

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);

  useHelpHotkey(useCallback(() => setHelpOpen(true), []));

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

  return (
    <div className="flex h-screen">
      <Sidebar
        projects={projects}
        selectedId={selectedId}
        onSelect={setSelectedId}
        loading={loading}
        error={error}
      />
      <Board className="flex-1" projectId={selectedId} />
      <HelpModal open={helpOpen} onOpenChange={setHelpOpen} />
    </div>
  );
}
