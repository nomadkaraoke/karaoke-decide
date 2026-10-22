"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  ReactNode,
} from "react";
import { useTranslations } from "next-intl";
import { api, ApiError, getAuthToken, setAuthToken, clearAuthToken } from "@/lib/api";
import { GUEST_TOKEN_KEY } from "@/lib/constants";
import * as genAuth from "@/lib/genAuth";

interface User {
  id: string;
  email: string | null;
  display_name: string | null;
  is_guest: boolean;
  is_admin: boolean;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  isVerified: boolean;
  isAdmin: boolean;
  hasCompletedQuiz: boolean;
  quizStatusLoading: boolean;
  login: (token: string) => Promise<void>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
  refreshQuizStatus: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function readGuestToken(): string | null {
  try {
    return localStorage.getItem(GUEST_TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeGuestToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(GUEST_TOKEN_KEY, token);
    else localStorage.removeItem(GUEST_TOKEN_KEY);
  } catch {
    // storage unavailable: nothing to claim later
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const t = useTranslations("auth");
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasCompletedQuiz, setHasCompletedQuiz] = useState(false);
  const [quizStatusLoading, setQuizStatusLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const claimInFlight = useRef(false);

  const refreshQuizStatus = useCallback(async () => {
    const token = getAuthToken();
    if (!token) {
      setHasCompletedQuiz(false);
      setQuizStatusLoading(false);
      return;
    }

    try {
      setQuizStatusLoading(true);
      const status = await api.quiz.getStatus();
      setHasCompletedQuiz(status.completed);
    } catch {
      // If we can't get status, assume not completed
      setHasCompletedQuiz(false);
    } finally {
      setQuizStatusLoading(false);
    }
  }, []);

  // Merge a legacy guest profile into the signed-in gen account. 200/400/404
  // are final (drop the stored guest token); anything else (5xx, network)
  // keeps it so the next checkAuth retries.
  const claimGuestIfAny = useCallback(async () => {
    const token = getAuthToken();
    const guestToken = readGuestToken();
    if (!guestToken || !token || token.includes(".") || claimInFlight.current) {
      return false;
    }
    claimInFlight.current = true;
    try {
      await api.auth.claimGuest(guestToken);
      writeGuestToken(null);
      setNotice(t("importedGuestData"));
      return true;
    } catch (err) {
      if (err instanceof ApiError && (err.status === 400 || err.status === 404)) {
        writeGuestToken(null);
      }
      return false;
    } finally {
      claimInFlight.current = false;
    }
  }, [t]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  const checkAuth = useCallback(async () => {
    const token = getAuthToken();
    if (!token) {
      setUser(null);
      setHasCompletedQuiz(false);
      setQuizStatusLoading(false);
      setIsLoading(false);
      return;
    }

    try {
      const userData = await api.auth.getMe();
      if (userData.is_guest) {
        // Guest sessions no longer exist. Treat as signed out but keep the
        // token: signing in parks it (see login) and claims its data.
        setUser(null);
        setHasCompletedQuiz(false);
        setQuizStatusLoading(false);
        return;
      }
      setUser(userData);
      // Fold any pre-sign-in guest data into this account before reading quiz status
      await claimGuestIfAny();
      // Also check quiz status
      await refreshQuizStatus();
    } catch {
      // Token is invalid or expired
      clearAuthToken();
      setUser(null);
      setHasCompletedQuiz(false);
      setQuizStatusLoading(false);
    } finally {
      setIsLoading(false);
    }
  }, [refreshQuizStatus, claimGuestIfAny]);

  const login = useCallback(async (token: string) => {
    // A legacy guest session is about to be replaced: park it so checkAuth
    // can claim its data into the new account.
    const existing = getAuthToken();
    if (existing && existing !== token && existing.includes(".")) {
      if (await api.auth.isGuestToken(existing)) writeGuestToken(existing);
    }
    setAuthToken(token);
    // Token is stored; caller should call checkAuth() to fetch user data
  }, []);

  const logout = useCallback(async () => {
    const token = getAuthToken();
    try {
      // Revoke the gen session (a legacy decide JWT has nothing to revoke)
      if (token && !token.includes(".")) await genAuth.logout(token);
    } catch {
      // Ignore errors on logout
    } finally {
      clearAuthToken();
      setUser(null);
      setHasCompletedQuiz(false);
    }
  }, []);

  // Check auth on mount
  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Listen for storage events (token changes in other tabs)
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === "karaoke_decide_token") {
        checkAuth();
      }
    };

    window.addEventListener("storage", handleStorageChange);
    return () => window.removeEventListener("storage", handleStorageChange);
  }, [checkAuth]);

  const isVerified = !!user && !user.is_guest;
  const isAdmin = user?.is_admin ?? false;

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        isVerified,
        isAdmin,
        hasCompletedQuiz,
        quizStatusLoading,
        login,
        logout,
        checkAuth,
        refreshQuizStatus,
      }}
    >
      {children}
      {notice && (
        <div
          role="status"
          data-testid="claim-guest-toast"
          className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-xl border border-[var(--card-border)] bg-[var(--card)] px-4 py-3 text-sm text-[var(--text)] shadow-lg"
        >
          {notice}
        </div>
      )}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
