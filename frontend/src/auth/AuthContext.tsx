import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { logoutAccount, restoreSession, type SessionUser } from "../api/auth";

type AuthState = {
  user: SessionUser | null;
  ready: boolean;
  setUser: (user: SessionUser | null) => void;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<SessionUser | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    restoreSession()
      .then((restored) => {
        if (!cancelled) {
          setUserState(restored);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setReady(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      ready,
      setUser: setUserState,
      logout: async () => {
        await logoutAccount();
        setUserState(null);
      },
    }),
    [ready, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("AuthProvider is missing");
  }
  return value;
}
