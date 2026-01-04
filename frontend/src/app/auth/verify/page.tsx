"use client";

import { useEffect, useState, Suspense, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, setAuthToken, NetworkError, ApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui";
import { CheckIcon, XIcon, LoaderIcon } from "@/components/icons";

type ErrorType = "network" | "expired" | "invalid" | "unknown";

function VerifyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { checkAuth } = useAuth();
  const [status, setStatus] = useState<"verifying" | "success" | "error">(
    "verifying"
  );
  const [error, setError] = useState<string | null>(null);
  const [errorType, setErrorType] = useState<ErrorType>("unknown");
  const [retryCount, setRetryCount] = useState(0);

  const verifyToken = useCallback(async () => {
    const token = searchParams.get("token");

    if (!token) {
      setStatus("error");
      setErrorType("invalid");
      setError("No verification token found. Please request a new magic link.");
      return;
    }

    setStatus("verifying");
    setError(null);

    try {
      const response = await api.auth.verifyToken(token);
      setAuthToken(response.access_token);
      setStatus("success");

      // Refresh auth context
      await checkAuth();

      // Redirect after a brief delay to show success
      setTimeout(() => {
        router.push("/my-songs");
      }, 1500);
    } catch (err) {
      setStatus("error");

      if (NetworkError.isNetworkError(err)) {
        setErrorType("network");
        setError(err.message);
      } else if (err instanceof ApiError) {
        if (err.message.includes("expired")) {
          setErrorType("expired");
          setError("This magic link has expired. Please request a new one.");
        } else if (err.message.includes("already been used")) {
          setErrorType("invalid");
          setError("This magic link has already been used. Please request a new one.");
        } else {
          setErrorType("invalid");
          setError(err.message);
        }
      } else {
        setErrorType("unknown");
        setError(
          err instanceof Error
            ? err.message
            : "Failed to verify token. It may have expired or already been used."
        );
      }
    }
  }, [searchParams, checkAuth, router]);

  useEffect(() => {
    // Data fetching on mount is a valid use case for setState in useEffect
    // eslint-disable-next-line
    verifyToken();
  }, [verifyToken]);

  const handleRetry = () => {
    setRetryCount((c) => c + 1);
    verifyToken();
  };

  if (status === "verifying") {
    return (
      <main className="min-h-screen flex items-center justify-center px-4">
        <div className="text-center">
          <div className="relative w-20 h-20 mx-auto mb-6">
            <div className="absolute inset-0 bg-[#ff2d92]/20 rounded-full animate-pulse" />
            <div className="relative w-full h-full rounded-full bg-[#ff2d92]/10 flex items-center justify-center border border-[#ff2d92]/30">
              <LoaderIcon className="w-10 h-10 text-[#ff2d92] animate-spin" />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-white mb-2">
            Verifying your link...
          </h1>
          <p className="text-white/60">Please wait a moment</p>
        </div>
      </main>
    );
  }

  if (status === "success") {
    return (
      <main className="min-h-screen flex items-center justify-center px-4">
        <div className="text-center">
          <div className="relative w-20 h-20 mx-auto mb-6">
            <div className="absolute inset-0 bg-green-500/20 rounded-full animate-pulse" />
            <div className="relative w-full h-full rounded-full bg-green-500/10 flex items-center justify-center border border-green-500/30">
              <CheckIcon className="w-10 h-10 text-green-400" />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-white mb-2">
            You&apos;re signed in!
          </h1>
          <p className="text-white/60">Redirecting you to your songs...</p>
        </div>
      </main>
    );
  }

  // Error state
  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md text-center">
        <div className="relative w-20 h-20 mx-auto mb-6">
          <div className="absolute inset-0 bg-red-500/20 rounded-full" />
          <div className="relative w-full h-full rounded-full bg-red-500/10 flex items-center justify-center border border-red-500/30">
            <XIcon className="w-10 h-10 text-red-400" />
          </div>
        </div>

        <h1 className="text-2xl font-bold text-white mb-2">
          {errorType === "network" ? "Connection Error" : "Verification Failed"}
        </h1>
        <p className="text-white/60 mb-6">{error}</p>

        {/* Network error troubleshooting tips */}
        {errorType === "network" && (
          <div className="bg-white/5 rounded-lg p-4 mb-6 text-left">
            <p className="text-white/80 text-sm font-medium mb-2">Troubleshooting tips:</p>
            <ul className="text-white/60 text-sm space-y-1 list-disc list-inside">
              <li>Try using mobile data instead of WiFi</li>
              <li>Disable VPN or ad-blocker extensions</li>
              <li>Try opening in incognito/private mode</li>
              <li>Check if you&apos;re on a corporate or school network</li>
            </ul>
          </div>
        )}

        <div className="space-y-3">
          {/* Retry button for network errors */}
          {errorType === "network" && (
            <Button
              variant="primary"
              className="w-full"
              onClick={handleRetry}
            >
              {retryCount > 0 ? `Retry Again (${retryCount})` : "Try Again"}
            </Button>
          )}

          <Link href="/login">
            <Button
              variant={errorType === "network" ? "ghost" : "primary"}
              className="w-full"
            >
              Request New Link
            </Button>
          </Link>
          <Link href="/">
            <Button variant="ghost" className="w-full">
              Back to Home
            </Button>
          </Link>
        </div>
      </div>
    </main>
  );
}

export default function VerifyPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen flex items-center justify-center px-4">
          <div className="text-center">
            <div className="relative w-20 h-20 mx-auto mb-6">
              <div className="absolute inset-0 bg-[#ff2d92]/20 rounded-full animate-pulse" />
              <div className="relative w-full h-full rounded-full bg-[#ff2d92]/10 flex items-center justify-center border border-[#ff2d92]/30">
                <LoaderIcon className="w-10 h-10 text-[#ff2d92] animate-spin" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-white mb-2">Loading...</h1>
          </div>
        </main>
      }
    >
      <VerifyContent />
    </Suspense>
  );
}
