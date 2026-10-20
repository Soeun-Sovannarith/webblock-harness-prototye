/** Thin client for the WebBlock Harness API. The token is a session JWT (dev sign-in or the WebBlock auth service). */

export type Session = { token: string; user_id: string; tenant_id: string; expires_at: number };

export type SiteConfig = { pages: Record<string, PuckData> };
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type PuckData = { root: { props?: Record<string, any> }; content: any[]; zones?: Record<string, any[]> };

export type Project = {
  id: string;
  name: string;
  site_key: string;
  repository: string;
  repository_url: string;
  repository_created: boolean;
  deployed: boolean;
  template_id: string | null;
  default_branch: string;
  site_url: string | null;
  code_update_available: boolean;
  pointers: Record<string, string | null>;
  created_at: string;
};

export type Revision = {
  id: string;
  number: number;
  status: string;
  content_hash: string;
  config?: SiteConfig;
  validation?: { checks: { name: string; status: string; issues: { code: string; message: string; location?: string }[] }[] } | null;
};

export type Job = {
  id: string;
  kind: string;
  plan_hash: string | null;
  state: string;
  path: string | null;
  outcome: string | null;
  merged_commit_sha: string | null;
  error: { code: string; message: string; hint?: string | null; details?: Record<string, unknown> } | null;
  plan: { summary: string; changes: { kind: string; component_id?: string; prop_path?: (string | number)[]; before?: unknown; after?: unknown }[] } | null;
};

export type JobEvent = { id: number; code: string; level: string; message: string; to_state: string | null; created_at: string };

export type Template = { id: string; name: string; description: string; theme: string; best_for: string; preview: SiteConfig };

export type ApiError = { status: number; code: string; message: string; details?: Record<string, unknown> };

const SESSION_KEY = "webblock.session";

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    const s = raw ? (JSON.parse(raw) as Session) : null;
    return s && s.expires_at * 1000 > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null) {
  try {
    if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable: session lasts for this tab only */
  }
}

async function request<T>(method: string, path: string, body?: unknown, token?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = data?.error ?? data?.detail ?? {};
    throw { status: res.status, code: err.code ?? "error", message: err.message ?? res.statusText, details: err.details } as ApiError;
  }
  return data as T;
}

export class Api {
  constructor(private token: string) {}

  static devSignIn(email: string) {
    return request<Session>("POST", "/v1/dev/session", { email });
  }

  businessTypes() {
    return request<string[]>("GET", "/v1/business-types", undefined, this.token);
  }
  updateProject(id: string, patch: { site_url: string | null }) {
    return request<Project>("PATCH", `/v1/projects/${id}`, patch, this.token);
  }
  portalSession(id: string) {
    return request<{ portal_url: string; site_url: string; expires_at: number }>("POST", `/v1/projects/${id}/portal-session`, undefined, this.token);
  }
  templates() {
    return request<Template[]>("GET", "/v1/templates", undefined, this.token);
  }
  projects() {
    return request<Project[]>("GET", "/v1/projects", undefined, this.token);
  }
  project(id: string) {
    return request<Project>("GET", `/v1/projects/${id}`, undefined, this.token);
  }
  createSite(templateId: string, profile: unknown) {
    return request<Project>("POST", "/v1/sites", { template_id: templateId, profile }, this.token);
  }
  registry(id: string) {
    return request<Registry>("GET", `/v1/projects/${id}/registry`, undefined, this.token);
  }
  sync(id: string) {
    return request<{ status: string; draft?: string; commit_sha?: string; issues?: unknown[]; error?: string }>(
      "POST", `/v1/projects/${id}/sync`, undefined, this.token);
  }
  draft(id: string) {
    return request<Revision>("GET", `/v1/projects/${id}/draft`, undefined, this.token);
  }
  saveDraft(id: string, config: SiteConfig, expectedDraftRevisionId: string) {
    return request<Revision>("PUT", `/v1/projects/${id}/draft`, { config, expected_draft_revision_id: expectedDraftRevisionId }, this.token);
  }
  updateCode(id: string) {
    return request<Job>("POST", `/v1/projects/${id}/update-code`, undefined, this.token);
  }
  deploy(id: string) {
    return request<Job>("POST", `/v1/projects/${id}/deploy`, undefined, this.token);
  }
  job(id: string) {
    return request<Job>("GET", `/v1/jobs/${id}`, undefined, this.token);
  }
  events(id: string) {
    return request<JobEvent[]>("GET", `/v1/jobs/${id}/events`, undefined, this.token);
  }
  approve(id: string, planHash: string) {
    return request<Job>("POST", `/v1/jobs/${id}/approve`, { plan_hash: planHash }, this.token);
  }
  retry(id: string) {
    return request<Job>("POST", `/v1/jobs/${id}/retry`, undefined, this.token);
  }
  jobs(projectId: string) {
    return request<Job[]>("GET", `/v1/projects/${projectId}/jobs?limit=10`, undefined, this.token);
  }
}

export type FieldSpec = {
  type: "text" | "textarea" | "number" | "boolean" | "url" | "image" | "color" | "select" | "array" | "object" | "slot";
  label?: string | null;
  required?: boolean;
  options?: (string | number)[] | null;
  min?: number | null;
  max?: number | null;
  max_length?: number | null;
  item_fields?: Record<string, FieldSpec> | null;
};
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ComponentSpec = { name: string; label: string; description: string; fields: Record<string, FieldSpec>; default_props: Record<string, any> };
export type Registry = { hash: string; root: ComponentSpec; components: ComponentSpec[] };
