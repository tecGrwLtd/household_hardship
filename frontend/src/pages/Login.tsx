import { useState, type FormEvent } from "react";
import { useAuth } from "../state/auth";
import { Button, Field, Input } from "../components/ui";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="stack" style={{ gap: 2 }}>
          <b className="serif" style={{ fontSize: 28, color: "var(--teal)" }}>Hardship Fund</b>
          <span className="muted">Allocation platform · sign in</span>
        </div>
        <Field label="Username"><Input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus /></Field>
        <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        {error && <div className="callout error" role="alert">{error}</div>}
        <Button kind="primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
        <p className="small muted" style={{ margin: 0 }}>
          Demo accounts: <span className="mono">admin</span> / <span className="mono">admin-dev-only</span> (programme manager),{" "}
          <span className="mono">uwase</span> / <span className="mono">caseworker-dev-only</span> (caseworker).
        </p>
      </form>
    </div>
  );
}
