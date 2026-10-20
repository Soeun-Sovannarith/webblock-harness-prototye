import { useEffect, useState } from "react";

import { Api, loadSession, saveSession, type Session } from "./api";
import { CreateSite } from "./views/CreateSite";
import { Dashboard } from "./views/Dashboard";
import { Editor } from "./views/Editor";
import { Login } from "./views/Login";

function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export function navigate(to: string) {
  window.location.hash = to;
}

export function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const route = useHashRoute();

  if (!session) {
    return (
      <Login
        onSignedIn={(s) => {
          saveSession(s);
          setSession(s);
        }}
      />
    );
  }
  const api = new Api(session.token);
  const signOut = () => {
    saveSession(null);
    setSession(null);
  };

  const edit = route.match(/^#\/edit\/([\w-]+)/);
  if (edit) return <Editor api={api} projectId={edit[1]} />;
  if (route.startsWith("#/new")) return <CreateSite api={api} />;
  return <Dashboard api={api} session={session} onSignOut={signOut} />;
}
