import { Puck, type Data } from "@puckeditor/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { Api, ApiError, Project, Registry, Revision, SiteConfig } from "../api";
import { navigate } from "../App";
import { buildPuckConfig } from "../puckConfig";
import { DeployPanel } from "./DeployPanel";

type Notice = { kind: "info" | "warn" | "error"; text: string } | null;

function validationIssues(rev: Revision): string[] {
  return (rev.validation?.checks ?? []).flatMap((c) => c.issues.map((i) => `${i.message}${i.location ? ` (${i.location})` : ""}`));
}

export function Editor({ api, projectId }: { api: Api; projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [site, setSite] = useState<SiteConfig | null>(null);
  const [page, setPage] = useState("/");
  const [notice, setNotice] = useState<Notice>(null);
  const [savedJson, setSavedJson] = useState("");
  const [busy, setBusy] = useState<"saving" | "deploying" | null>(null);
  const [deployJobId, setDeployJobId] = useState<string | null>(null);
  const [loadKey, setLoadKey] = useState(0);
  const siteRef = useRef<SiteConfig | null>(null);
  const draftIdRef = useRef("");

  const load = useCallback(async () => {
    try {
      const sync = await api.sync(projectId).catch(() => null);
      if (sync?.status === "imported") {
        setNotice({ kind: "info", text: sync.draft === "conflict"
          ? "Content was edited on GitHub and conflicts with your unpublished changes. The GitHub version is live; your draft was kept — review it before deploying."
          : "Loaded the latest content from GitHub (someone edited it there)." });
      } else if (sync?.status === "invalid_repo_config") {
        setNotice({ kind: "warn", text: "The content file on GitHub was edited into an invalid state, so it was not loaded. Deploying will be blocked until it is fixed or reverted." });
      }
      const [p, reg, draft] = await Promise.all([api.project(projectId), api.registry(projectId), api.draft(projectId)]);
      setProject(p);
      setRegistry(reg);
      draftIdRef.current = draft.id;
      siteRef.current = draft.config!;
      setSite(draft.config!);
      setSavedJson(JSON.stringify(draft.config));
      setPage((current) => (draft.config!.pages[current] ? current : Object.keys(draft.config!.pages)[0]));
      setLoadKey((k) => k + 1);
    } catch (err) {
      const e = err as ApiError;
      if (e.status === 404) navigate("#/");
      setNotice({ kind: "error", text: e.message });
    }
  }, [api, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const config = useMemo(() => (registry ? buildPuckConfig(registry) : null), [registry]);
  const dirty = site !== null && JSON.stringify(site) !== savedJson;

  const save = useCallback(async (): Promise<boolean> => {
    const current = siteRef.current;
    if (!current) return false;
    const json = JSON.stringify(current);
    if (json === savedJson) return true;
    setBusy("saving");
    try {
      const rev = await api.saveDraft(projectId, current, draftIdRef.current);
      draftIdRef.current = rev.id;
      setSavedJson(json);
      const issues = validationIssues(rev);
      setNotice(issues.length ? { kind: "warn", text: `Saved, but fix before deploying: ${issues.slice(0, 3).join("; ")}` } : { kind: "info", text: "Draft saved." });
      return issues.length === 0;
    } catch (err) {
      const e = err as ApiError;
      setNotice({ kind: "error", text: e.code === "stale_draft" ? "This site was changed in another tab. Reload to continue." : e.message });
      return false;
    } finally {
      setBusy(null);
    }
  }, [api, projectId, savedJson]);

  const deploy = useCallback(async () => {
    if (!(await save())) return;
    setBusy("deploying");
    try {
      const job = await api.deploy(projectId);
      setDeployJobId(job.id);
    } catch (err) {
      const e = err as ApiError;
      setNotice({ kind: e.code === "nothing_to_deploy" ? "info" : "error", text: e.message });
    } finally {
      setBusy(null);
    }
  }, [api, projectId, save]);

  if (!site || !config || !project) {
    return (
      <main className="app-center">
        <p className="muted">{notice?.text ?? "Loading editor…"}</p>
      </main>
    );
  }

  const pages = Object.keys(site.pages);

  return (
    <div className="editor">
      {notice ? (
        <div className={`notice ${notice.kind}`} role="status">
          <span>{notice.text}</span>
          <button className="btn ghost small" onClick={() => setNotice(null)} aria-label="Dismiss">
            ✕
          </button>
        </div>
      ) : null}
      <Puck
        key={`${page}-${loadKey}`}
        config={config}
        data={site.pages[page] as Partial<Data>}
        headerTitle={project.name}
        onChange={(data) => {
          setSite((prev) => {
            const next = { pages: { ...(prev ?? site).pages, [page]: data as SiteConfig["pages"][string] } };
            siteRef.current = next;
            return next;
          });
        }}
        overrides={{
          headerActions: () => (
            <div className="editor-actions">
              <button className="btn ghost small" onClick={() => navigate("#/")}>
                ← Sites
              </button>
              <select className="page-select" value={page} onChange={(e) => setPage(e.target.value)} aria-label="Page">
                {pages.map((p) => (
                  <option key={p} value={p}>
                    {p === "/" ? "Home" : p}
                  </option>
                ))}
              </select>
              <span className="muted small">{dirty ? "Unsaved changes" : "Saved"}</span>
              <button className="btn ghost small" disabled={busy !== null || !dirty} onClick={() => void save()}>
                {busy === "saving" ? "Saving…" : "Save draft"}
              </button>
              <button className="btn primary small" disabled={busy !== null} onClick={() => void deploy()}>
                {busy === "deploying" ? "Starting…" : project.repository_created ? "Deploy changes" : "Deploy to GitHub"}
              </button>
            </div>
          ),
        }}
      />
      {deployJobId ? (
        <DeployPanel
          api={api}
          jobId={deployJobId}
          project={project}
          onClose={(changed) => {
            setDeployJobId(null);
            if (changed) void load();
          }}
        />
      ) : null}
    </div>
  );
}
