"use client";

// Token in memory + sessionStorage (docs/V2/FRONTEND_DESIGN.md §9: acceptable for a short-lived
// token in development; an httpOnly cookie set by the API is the production upgrade path).
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import type { User } from "./types";

const KEY = "autohub.token";

interface AuthState {
  token: string | null;
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const SIGNED_OUT: AuthState = {
  token: null,
  user: null,
  ready: true,
  login: async () => {
    throw new Error("Sign-in is unavailable here.");
  },
  logout: () => {},
};

function readToken(): string | null {
  try {
    return window.sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function writeToken(token: string | null): void {
  try {
    if (token) window.sessionStorage.setItem(KEY, token);
    else window.sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: token stays in memory only */
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = readToken();
    if (!stored) {
      setReady(true);
      return;
    }
    api
      .me(stored)
      .then((me) => {
        setToken(stored);
        setUser(me);
      })
      .catch(() => writeToken(null))
      .finally(() => setReady(true));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login(email, password);
    const me = await api.me(result.access_token);
    writeToken(result.access_token);
    setToken(result.access_token);
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    writeToken(null);
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo(() => ({ token, user, ready, login, logout }), [token, user, ready, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  // Outside the provider (e.g. an isolated server render) behave as signed out rather than crash.
  return useContext(AuthContext) ?? SIGNED_OUT;
}
