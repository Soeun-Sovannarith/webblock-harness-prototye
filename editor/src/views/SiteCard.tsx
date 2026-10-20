import { useState, type FormEvent } from "react";

import type { Api, ApiError, Project } from "../api";
import { navigate } from "../App";

const DEFAULT_SITE_URL = "http://localhost:3000";
const DONE = new Set(["succeeded", "failed", "cancelled", "needs_clarification", "awaiting_approval"]);

/** One website on the dashboard: editor, repository, and the Business Portal (products & contacts). */
export function SiteCard({ api, project: p, onChange }: { api: Api; project: Project; onChange: (p: Project) => void }) {
  const [editingUrl, setEditingUrl] = useState(false);
  const [url, setUrl] = useState(p.site_url ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [updateNote, setUpdateNote] = useState<string | null>(null);

  async function updateCode() {
    setUpdating(true);
    setMessage(null);
    try {
      let job = await api.updateCode(p.id);
      while (!DONE.has(job.state)) {
        await new Promise((r) => setTimeout(r, 1000));
        job = await api.job(job.id);
      }
      if (job.state !== "succeeded") {
        setMessage(job.error ? `${job.error.message}${job.error.hint ? ` ${job.error.hint}` : ""}` : `Update stopped (${job.state}).`);
      } else {
        const events = await api.events(job.id);
        const note = events.find((e) => e.code === "site_code_updated" || e.code === "no_changes");
        setUpdateNote(note?.message ?? "Site code updated.");
      }
      onChange(await api.project(p.id));
    } catch (err) {
      setMessage((err as ApiError).message);
    } finally {
      setUpdating(false);
    }
  }

  async function openPortal() {
    setMessage(null);
    try {
      const session = await api.portalSession(p.id);
      window.open(session.portal_url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setMessage((err as ApiError).message);
    }
  }

  async function saveUrl(e: FormEvent) {
    e.preventDefault();
    try {
      onChange(await api.updateProject(p.id, { site_url: url.trim() || null }));
      setEditingUrl(false);
      setMessage(null);
    } catch (err) {
      setMessage((err as ApiError).message);
    }
  }

  return (
    <article className="panel site-card">
      <div className="site-card__head">
        <h2>{p.name}</h2>
        <span className={p.deployed ? "badge ok" : "badge"}>{p.deployed ? "On GitHub" : "Not deployed yet"}</span>
      </div>
      <p className="muted small mono">{p.repository}</p>
      <div className="row">
        <button className="btn primary small" onClick={() => navigate(`#/edit/${p.id}`)}>
          Open editor
        </button>
        {p.repository_created ? (
          <>
            <button className="btn ghost small" onClick={() => void openPortal()} title="Manage products, business info and contacts">
              Business Portal ↗
            </button>
            <a className="btn ghost small" href={p.repository_url} target="_blank" rel="noreferrer">
              Repository ↗
            </a>
          </>
        ) : null}
      </div>
      {p.repository_created ? (
        editingUrl ? (
          <form className="row" onSubmit={saveUrl}>
            <input className="url-input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={DEFAULT_SITE_URL} aria-label="Site URL" />
            <button className="btn primary small">Save</button>
            <button type="button" className="btn ghost small" onClick={() => setEditingUrl(false)}>Cancel</button>
          </form>
        ) : (
          <p className="muted small">
            Business Portal opens on <span className="mono">{p.site_url ?? DEFAULT_SITE_URL}</span>{p.site_url ? null : " — run the site there first (npm run dev)"}{" "}
            <button className="link-btn" onClick={() => setEditingUrl(true)}>change</button>
          </p>
        )
      ) : (
        <p className="muted small">Deploy once to create the repository; the Business Portal ships with the site.</p>
      )}
      {p.code_update_available ? (
        <div className="notice small">
          <p>
            This site was deployed with older WebBlock code (for example, before the Business Portal and database were added).
            Updating pushes the new code to GitHub. Files you edited on GitHub and your page content are left as they are.
          </p>
          <button className="btn primary small" disabled={updating} onClick={() => void updateCode()}>
            {updating ? "Updating…" : "Update site code"}
          </button>
        </div>
      ) : null}
      {updateNote ? <p className="muted small">{updateNote}</p> : null}
      {message ? <p className="error small">{message}</p> : null}
    </article>
  );
}
