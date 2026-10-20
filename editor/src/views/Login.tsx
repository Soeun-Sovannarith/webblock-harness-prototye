import { useState, type FormEvent } from "react";

import { Api, type ApiError, type Session } from "../api";

export function Login({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await Api.devSignIn(email));
    } catch (err) {
      const apiErr = err as ApiError;
      setError(
        apiErr.status === 404
          ? "Local sign-in is turned off. Set DEV_LOGIN_ENABLED=true in the harness .env (development only), or sign in through WebBlock."
          : apiErr.message,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-center">
      <form className="panel narrow" onSubmit={submit}>
        <p className="eyebrow">WebBlock</p>
        <h1 className="display">Build your business website</h1>
        <p className="muted">Sign in with your email to create and edit your sites.</p>
        <label className="field">
          Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@business.com" autoFocus />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button className="btn primary" disabled={busy}>
          {busy ? "Signing in…" : "Continue"}
        </button>
        <p className="fine">Development sign-in. In production, identity comes from the WebBlock auth service.</p>
      </form>
    </main>
  );
}
