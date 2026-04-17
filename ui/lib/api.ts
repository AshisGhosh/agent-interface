import type { Project, Task, TaskEvent, TaskPatch } from "@/lib/types";

export type { Project, Task, TaskEvent, TaskPatch };

const API_BASE = "/api";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ── Projects ──────────────────────────────────────────────────────────────

export async function listProjects(): Promise<Project[]> {
  const res = await fetch(`${API_BASE}/projects`, { cache: "no-store" });
  return handle<Project[]>(res);
}

export const fetchProjects = listProjects;

export async function createProject(
  name: string,
  description?: string,
): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description }),
  });
  return handle<Project>(res);
}

// ── Tasks ─────────────────────────────────────────────────────────────────

export async function listProjectTasks(
  projectId: string,
  { includeClosed = false }: { includeClosed?: boolean } = {},
): Promise<Task[]> {
  const qs = includeClosed ? "?include_closed=true" : "";
  const res = await fetch(`${API_BASE}/projects/${projectId}/tasks${qs}`, {
    cache: "no-store",
  });
  return handle<Task[]>(res);
}

export const fetchProjectTasks = listProjectTasks;

export async function getTask(taskId: string): Promise<Task> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, { cache: "no-store" });
  return handle<Task>(res);
}

export async function createTask(body: {
  project: string;
  title: string;
  description?: string | null;
  priority?: number;
  tags?: string[];
  depends_on?: string[];
}): Promise<Task> {
  const res = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<Task>(res);
}

export async function patchTask(
  taskId: string,
  patch: TaskPatch,
): Promise<Task> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return handle<Task>(res);
}

export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}`, { method: "DELETE" });
  return handle<void>(res);
}

// ── Events ────────────────────────────────────────────────────────────────

export async function listTaskEvents(taskId: string): Promise<TaskEvent[]> {
  const res = await fetch(`${API_BASE}/tasks/${taskId}/events`, {
    cache: "no-store",
  });
  return handle<TaskEvent[]>(res);
}

// ── Dispatch ──────────────────────────────────────────────────────────────

export interface DispatchResult {
  dispatched: number;
  agents: {
    task_id: string;
    session_id: string;
    tmux_target: string;
    worktree_path: string | null;
  }[];
}

export async function dispatchAgents(
  project: string,
  n: number = 1,
  opts?: { worktree?: boolean; tags?: string[] },
): Promise<DispatchResult> {
  const res = await fetch(`${API_BASE}/dispatch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project,
      n,
      worktree: opts?.worktree ?? true,
      tags: opts?.tags ?? [],
    }),
  });
  return handle<DispatchResult>(res);
}
