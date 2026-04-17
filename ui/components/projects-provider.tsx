"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  createProject as apiCreateProject,
  fetchProjectTasks,
  fetchProjects,
  type Project,
} from "@/lib/api";

export type ProjectSummary = {
  total: number;
  open: number;
  byStatus: Record<string, number>;
};

type ProjectsContextValue = {
  projects: Project[];
  summaries: Record<string, ProjectSummary>;
  selectedProjectId: string | null;
  selectProject: (id: string) => void;
  createProject: (name: string, description?: string) => Promise<Project>;
  refresh: () => Promise<void>;
  loading: boolean;
  error: string | null;
};

const STORAGE_KEY = "agi.selectedProjectId";

const ProjectsContext = createContext<ProjectsContextValue | null>(null);

export function useProjects(): ProjectsContextValue {
  const ctx = useContext(ProjectsContext);
  if (!ctx) {
    throw new Error("useProjects must be used within ProjectsProvider");
  }
  return ctx;
}

function readStoredProjectId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredProjectId(id: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id) window.localStorage.setItem(STORAGE_KEY, id);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore persistence failures (private mode, etc.)
  }
}

async function loadSummaries(
  projects: Project[],
): Promise<Record<string, ProjectSummary>> {
  const entries = await Promise.all(
    projects.map(async (p): Promise<[string, ProjectSummary]> => {
      try {
        const tasks = await fetchProjectTasks(p.id, { includeClosed: true });
        const byStatus: Record<string, number> = {};
        let open = 0;
        for (const t of tasks) {
          byStatus[t.status] = (byStatus[t.status] ?? 0) + 1;
          if (t.status !== "done") open += 1;
        }
        return [p.id, { total: tasks.length, open, byStatus }];
      } catch {
        return [p.id, { total: 0, open: 0, byStatus: {} }];
      }
    }),
  );
  return Object.fromEntries(entries);
}

export function ProjectsProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [summaries, setSummaries] = useState<Record<string, ProjectSummary>>({});
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const resolveSelection = useCallback(
    (list: Project[], current: string | null): string | null => {
      if (current && list.some((p) => p.id === current)) return current;
      const stored = readStoredProjectId();
      if (stored && list.some((p) => p.id === stored)) return stored;
      return list[0]?.id ?? null;
    },
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchProjects();
      setProjects(list);
      setError(null);
      const nextSummaries = await loadSummaries(list);
      setSummaries(nextSummaries);
      setSelectedProjectId((prev) => resolveSelection(list, prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [resolveSelection]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    writeStoredProjectId(selectedProjectId);
  }, [selectedProjectId]);

  const selectProject = useCallback((id: string) => {
    setSelectedProjectId(id);
  }, []);

  const createProject = useCallback(
    async (name: string, description?: string) => {
      const project = await apiCreateProject(name, description);
      setProjects((prev) =>
        prev.some((p) => p.id === project.id) ? prev : [...prev, project],
      );
      setSummaries((prev) => ({
        ...prev,
        [project.id]: { total: 0, open: 0, byStatus: {} },
      }));
      setSelectedProjectId(project.id);
      return project;
    },
    [],
  );

  const value = useMemo<ProjectsContextValue>(
    () => ({
      projects,
      summaries,
      selectedProjectId,
      selectProject,
      createProject,
      refresh,
      loading,
      error,
    }),
    [
      projects,
      summaries,
      selectedProjectId,
      selectProject,
      createProject,
      refresh,
      loading,
      error,
    ],
  );

  return (
    <ProjectsContext.Provider value={value}>
      {children}
    </ProjectsContext.Provider>
  );
}
