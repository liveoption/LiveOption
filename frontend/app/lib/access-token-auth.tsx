import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type AuthState = {
  authenticated: boolean;
  loading: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState>({
  authenticated: false,
  loading: true,
  refresh: async () => {},
  signOut: async () => {},
});

export function AccessTokenProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    try {
      const response = await fetch("/api/session", { cache: "no-store" });
      const data = await response.json();
      setAuthenticated(data.authenticated === true);
    } catch {
      setAuthenticated(false);
    } finally {
      setLoading(false);
    }
  }

  async function signOut() {
    await fetch("/api/session", { method: "DELETE" });
    setAuthenticated(false);
    window.location.assign("/");
  }

  useEffect(() => { void refresh(); }, []);
  return (
    <AuthContext.Provider value={{ authenticated, loading, refresh, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAccessAuth() {
  return useContext(AuthContext);
}

export function SignedIn({ children }: { children: ReactNode }) {
  const { authenticated, loading } = useAccessAuth();
  return !loading && authenticated ? <>{children}</> : null;
}

export function SignedOut({ children }: { children: ReactNode }) {
  const { authenticated, loading } = useAccessAuth();
  return !loading && !authenticated ? <>{children}</> : null;
}
