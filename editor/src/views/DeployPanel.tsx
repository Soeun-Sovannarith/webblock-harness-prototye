import { useEffect, useState } from "react";

import type { Api, ApiError, Job, JobEvent, Project } from "../api";

const DONE = new Set(["succeeded", "failed", "cancelled", "needs_clarification", "awaiting_approval"]);
const CHANGE_STAGES: [string, string][] = [
  ["analyzing", "Working out what changed"],
  ["applying", "Applying your edits"],
  ["validating", "Checking the content"],
  ["committing", "Pushing to GitHub"],
  ["deploying", "Publishing"],
  ["verifying", "Verifying on GitHub"],
];
const PROVISION_STAGES: [string, string][] = [
  ["committing", "Creating the repository and pushing your site"],
  ["deploying", "Publishing"],
  ["verifying", "Verifying on GitHub"],
];

export function DeployPanel({ api, jobId, project, onClose }: {
  api: Api; jobId: string; project: Project; onClose: (changed: boolean) => void;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pollRun, setPollRun] = useState(0); // bump to restart polling after retry/approve

  useEffect(() => {
    let stop = false;
    async function poll() {
      try {
        const [j, ev] = await Promise.all([api.job(jobId), api.events(jobId)]);
        if (stop) return;
        setJob(j);
        setEvents(ev);
        if (!DONE.has(j.state)) setTimeout(poll, 800);
      } catch (err) {
        setError((err as ApiError).message);
      }
    }
    void poll();
    return () => {
      stop = true;
    };
  }, [api, jobId, pollRun]);

  const reached = new Set(events.map((e) => e.to_state).filter(Boolean));
  const commitUrl = job?.merged_commit_sha ? `${project.repository_url}/commit/${job.merged_commit_sha}` : null;

  async function retry() {
    try {
      setJob(await api.retry(jobId));
      setPollRun((n) => n + 1);
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  async function approve() {
    if (!job?.plan_hash) return;
    try {
      setJob(await api.approve(job.id, job.plan_hash));
      setPollRun((n) => n + 1);
    } catch (err) {
      setError((err as ApiError).message);
    }
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="deploy-title">
      <div className="panel deploy">
        <h2 id="deploy-title">{job?.kind === "provision" ? "Deploying your website" : "Deploying your changes"}</h2>
        <p className="muted small mono">{project.repository}</p>
        <ol className="stages">
          {(job?.kind === "provision" ? PROVISION_STAGES : CHANGE_STAGES).map(([state, label]) => (
            <li key={state} className={reached.has(state) ? "done" : job?.state === state ? "active" : ""}>
              {label}
            </li>
          ))}
        </ol>

        {job?.state === "succeeded" && job.outcome === "no_changes" ? (
          <p className="notice info">Nothing to publish — GitHub already has this content.</p>
        ) : null}
        {job?.state === "succeeded" && job.outcome !== "no_changes" ? (
          <div className="notice ok">
            <strong>{job.kind === "provision" ? "Your website is on GitHub." : "Your changes are on GitHub."}</strong>
            {job.plan?.changes?.length ? (
              <ul className="changes">
                {job.plan.changes.slice(0, 8).map((c, i) => (
                  <li key={i} className="small">
                    {c.kind.replace(/_/g, " ")} · {c.component_id}
                    {c.prop_path ? `.${c.prop_path.join(".")}` : ""}
                    {c.prop_path ? `: ${JSON.stringify(c.before)} → ${JSON.stringify(c.after)}` : ""}
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="row">
              {commitUrl ? <a className="btn primary small" href={commitUrl} target="_blank" rel="noreferrer">View commit ↗</a> : null}
              <a className="btn ghost small" href={project.repository_url} target="_blank" rel="noreferrer">Open repository ↗</a>
            </div>
          </div>
        ) : null}
        {job?.state === "failed" && job.error ? (
          <div className="notice error">
            <strong>{job.error.message}</strong>
            {job.error.hint ? <p className="small">{job.error.hint}</p> : null}
            {(job.error as { retryable?: boolean }).retryable ? (
              <button className="btn ghost small" onClick={() => void retry()}>Retry</button>
            ) : null}
          </div>
        ) : null}
        {job?.state === "awaiting_approval" ? (
          <div className="notice warn">
            <strong>This change removes a lot of content and needs your confirmation.</strong>
            <p className="small">{job.plan?.summary}</p>
            <button className="btn primary small" onClick={() => void approve()}>Confirm and deploy</button>
          </div>
        ) : null}
        {error ? <p className="error">{error}</p> : null}

        <details className="log">
          <summary className="small">Activity log ({events.length})</summary>
          <ul>
            {events.map((e) => (
              <li key={e.id} className={`small ${e.level}`}>
                <span className="mono">{new Date(e.created_at).toLocaleTimeString()}</span> {e.message}
              </li>
            ))}
          </ul>
        </details>

        <div className="row end">
          <button className="btn ghost" onClick={() => onClose(job?.state === "succeeded")} disabled={job !== null && !DONE.has(job.state)}>
            {job && DONE.has(job.state) ? "Back to editor" : "Working…"}
          </button>
        </div>
      </div>
    </div>
  );
}
