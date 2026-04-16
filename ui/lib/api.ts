import type { Project, Task, TaskCreate, TaskPatch } from "@/lib/types";

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

export async function listProjects(): Promise<Project[]> {
  const res = await fetch(`${API_BASE}/projects`, { cache: "no-store" });
  return handle<Project[]>(res);
}

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

export async function createTask(body: TaskCreate): Promise<Task> {
  const res = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handle<Task>(res);
}
