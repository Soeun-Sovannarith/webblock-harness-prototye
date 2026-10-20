import { useEffect, useState } from "react";

import { SiteCard } from "./SiteCard";

import type { Api, ApiError, Project, Session } from "../api";
import { navigate } from "../App";

export function Dashboard({ api, session, onSignOut }: { api: Api; session: Session; onSignOut: () => void }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.projects().then(setProjects, (e: ApiError) => (e.status === 401 ? onSignOut() : setError(e.message)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app-page">
      <header className="topbar">
        <span className="brand">WebBlock</span>
        <span className="muted small">{session.user_id}</span>
        <button className="btn ghost small" onClick={onSignOut}>
          Sign out
        </button>
      </header>
      <main className="container">
        <div className="page-head">
          <div>
            <h1 className="display">Your websites</h1>
            <p className="muted">Each site is a standalone Next.js project in the WebBlock GitHub organization.</p>
          </div>
          <button className="btn primary" onClick={() => navigate("#/new")}>
            Create a website
          </button>
        </div>
        {error ? <p className="error">{error}</p> : null}
        {projects === null ? <p className="muted">Loading…</p> : null}
        {projects?.length === 0 ? (
          <div className="panel empty">
            <h2>No websites yet</h2>
            <p className="muted">Tell us about your business, pick a template and edit it visually.</p>
            <button className="btn primary" onClick={() => navigate("#/new")}>
              Create your first website
            </button>
          </div>
        ) : null}
        <div className="cards">
          {projects?.map((p) => (
            <SiteCard key={p.id} api={api} project={p} onChange={(next) => setProjects((all) => all?.map((x) => (x.id === next.id ? next : x)) ?? null)} />
          ))}
        </div>
      </main>
    </div>
  );
}
