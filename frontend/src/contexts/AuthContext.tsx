"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  ReactNode,
} from "react";
import { api, getAuthToken, setAuthToken, clearAuthToken } from "@/lib/api";

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
  isGuest: boolean;
  isVerified: boolean;
  isAdmin: boolean;
  hasCompletedQuiz: boolean;
  quizStatusLoading: boolean;
  login: (token: string) => void;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
  startGuestSession: () => Promise<void>;
  requestUpgrade: (email: string) => Promise<void>;
  refreshQuizStatus: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasCompletedQuiz, setHasCompletedQuiz] = useState(false);
  const [quizStatusLoading, setQuizStatusLoading] = useState(true);

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
      setUser(userData);
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
  }, [refreshQuizStatus]);

  const login = useCallback((token: string) => {
    setAuthToken(token);
    // Token is stored; caller should call checkAuth() to fetch user data
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } catch {
      // Ignore errors on logout
    } finally {
      clearAuthToken();
      setUser(null);
      setHasCompletedQuiz(false);
    }
  }, []);

  const startGuestSession = useCallback(async () => {
    try {
      const response = await api.auth.createGuestSession();
      setAuthToken(response.access_token);
      await checkAuth();
    } catch (error) {
      console.error("Failed to create guest session:", error);
      throw error;
    }
  }, [checkAuth]);

  const requestUpgrade = useCallback(async (email: string) => {
    await api.auth.upgradeGuest(email);
    // Email sent - user needs to click link to verify
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

  const isGuest = user?.is_guest ?? false;
  const isVerified = !!user && !user.is_guest;
  const isAdmin = user?.is_admin ?? false;

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        isGuest,
        isVerified,
        isAdmin,
        hasCompletedQuiz,
        quizStatusLoading,
        login,
        logout,
        checkAuth,
        startGuestSession,
        requestUpgrade,
        refreshQuizStatus,
      }}
    >
      {children}
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
