import { useState, type FormEvent } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useAccessAuth } from "../lib/access-token-auth";
import { useLocale } from "../lib/locale";

export const Route = createFileRoute("/sign-in")({ component: SignInPage });

export function SignInPage() {
  const navigate = useNavigate();
  const { refresh } = useAccessAuth();
  const {zh, ja } = useLocale();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!response.ok) throw new Error("Invalid access token");
      setToken("");
      await refresh();
      await navigate({ to: "/runs/new" });
    } catch {
      setError(zh ? "访问令牌无效" : ja("Invalid access token"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto mt-20 max-w-md rounded-2xl border border-[var(--color-rule)] bg-[var(--color-paper)] p-8">
      <p className="lo-eyebrow">LIVEOPTION / ACCESS</p>
      <h1 className="mt-3 text-3xl font-semibold">{zh ? "输入访问令牌" : ja("Enter access token")}</h1>
      <p className="mt-3 text-sm text-[var(--color-ink-soft)]">{zh ? "登录后令牌只用于建立 HTTP-only 会话。" : ja("The token is exchanged for an HTTP-only session cookie.")}</p>
      <form onSubmit={submit} className="mt-8 space-y-4">
        <label htmlFor="access-token" className="block text-sm font-medium">{zh ? "访问令牌" : ja("Access token")}</label>
        <input id="access-token" type="password" autoComplete="off" required value={token}
          onChange={(event) => setToken(event.target.value)}
          className="w-full rounded-lg border border-[var(--color-rule)] bg-transparent px-4 py-3" />
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full rounded-lg bg-[var(--color-primary-600)] px-4 py-3 font-medium text-white disabled:opacity-50">
          {busy ? (zh ? "验证中…" : ja("Checking…")) : (zh ? "继续" : ja("Continue"))}
        </button>
      </form>
    </section>
  );
}
