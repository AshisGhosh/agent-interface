export type Project = {
  id: string;
  name: string;
  description: string | null;
  autonomy: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type Task = {
  id: string;
  project_id: string;
  title: string;
  status: string;
  description: string | null;
  priority: number;
  tags: string[];
  parent_id: string | null;
  creator: string;
  spawned_from_task: string | null;
  spawned_from_session: string | null;
  assigned_session_id: string | null;
  worktree_path: string | null;
  depends_on: string[];
  created_at: string;
  updated_at: string;
  closed_at: string | null;
};

const API_BASE = "/api";

type FastAPIValidationItem = { loc?: unknown[]; msg?: string };

async function parseError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = (detail as FastAPIValidationItem[])
        .map((d) => d?.msg)
        .filter((m): m is string => typeof m === "string");
      if (msgs.length > 0) return msgs.join(", ");
    }
  } catch {
    // fall through
  }
  return `${res.status} ${res.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(await parseError(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function fetchProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
}

export function createProject(
  name: string,
  description?: string,
): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify({
      name,
      description: description?.trim() ? description.trim() : null,
    }),
  });
}

export function fetchProjectTasks(
  projectId: string,
  opts: { includeClosed?: boolean } = {},
): Promise<Task[]> {
  const params = new URLSearchParams();
  if (opts.includeClosed) params.set("include_closed", "true");
  const qs = params.toString();
  return request<Task[]>(
    `/projects/${encodeURIComponent(projectId)}/tasks${qs ? `?${qs}` : ""}`,
  );
}
