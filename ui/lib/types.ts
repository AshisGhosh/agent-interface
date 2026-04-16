export type TaskStatus =
  | "backlog"
  | "ready"
  | "in_progress"
  | "review"
  | "blocked"
  | "done";

export interface Task {
  id: string;
  project_id: string;
  title: string;
  status: TaskStatus;
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
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  autonomy: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface TaskPatch {
  status?: TaskStatus;
  priority?: number;
  assigned_session_id?: string;
  clear_assignment?: boolean;
  block_reason?: string;
  block_needs?: string;
  done_summary?: string;
  title?: string;
  description?: string;
  clear_description?: boolean;
  tags?: string[];
}

export interface TaskEvent {
  id: number | null;
  task_id: string;
  event_type: string;
  actor: string;
  payload_json: string | null;
  created_at: string;
}

export interface SSETaskEvent {
  id: number | null;
  task_id: string;
  event_type: string;
  actor: string;
  payload: string | null;
  created_at: string;
}
