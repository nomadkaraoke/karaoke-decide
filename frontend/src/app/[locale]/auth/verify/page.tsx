"use client";

import { useEffect, useState, Suspense, useCallback } from "react";
import { Link, useRouter } from "@/i18n/routing";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { api, setAuthToken, NetworkError, ApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui";
import { CheckIcon, XIcon, LoaderIcon } from "@/components/icons";

type ErrorType = "network" | "expired" | "invalid" | "unknown";

function VerifyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const t = useTranslations("auth");
  const tCommon = useTranslations("common");
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
      setError(t("noTokenFound"));
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
          setError(t("linkExpired"));
        } else if (err.message.includes("already been used")) {
          setErrorType("invalid");
          setError(t("linkAlreadyUsed"));
        } else {
          setErrorType("invalid");
          setError(err.message);
        }
      } else {
        setErrorType("unknown");
        setError(
          err instanceof Error
            ? err.message
            : t("failedToVerify")
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
            <div className="absolute inset-0 bg-[var(--brand-pink)]/20 rounded-full animate-pulse" />
            <div className="relative w-full h-full rounded-full bg-[var(--brand-pink)]/10 flex items-center justify-center border border-[var(--brand-pink)]/30">
              <LoaderIcon className="w-10 h-10 text-[var(--brand-pink)] animate-spin" />
            </div>
          </div>
          <h1 className="text-2xl font-bold text-[var(--text)] mb-2">
            {t("verifyingLink")}
          </h1>
          <p className="text-[var(--text-muted)]">{t("pleaseWait")}</p>
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
          <h1 className="text-2xl font-bold text-[var(--text)] mb-2">
            {t("youreSignedIn")}
          </h1>
          <p className="text-[var(--text-muted)]">{t("redirectingToSongs")}</p>
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

        <h1 className="text-2xl font-bold text-[var(--text)] mb-2">
          {errorType === "network" ? t("connectionError") : t("verificationFailed")}
        </h1>
        <p className="text-[var(--text-muted)] mb-6">{error}</p>

        {/* Network error troubleshooting tips */}
        {errorType === "network" && (
          <div className="bg-[var(--card)] rounded-lg p-4 mb-6 text-left">
            <p className="text-[var(--text)] text-sm font-medium mb-2">{t("troubleshootingTips")}</p>
            <ul className="text-[var(--text-muted)] text-sm space-y-1 list-disc list-inside">
              <li>{t("tipMobileData")}</li>
              <li>{t("tipDisableVpn")}</li>
              <li>{t("tipIncognito")}</li>
              <li>{t("tipCorporateNetwork")}</li>
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
              {retryCount > 0 ? t("retryAgainCount", { count: retryCount }) : tCommon("tryAgain")}
            </Button>
          )}

          <Link href="/login">
            <Button
              variant={errorType === "network" ? "ghost" : "primary"}
              className="w-full"
            >
              {t("requestNewLink")}
            </Button>
          </Link>
          <Link href="/">
            <Button variant="ghost" className="w-full">
              {t("backToHomeCaps")}
            </Button>
          </Link>
        </div>
      </div>
    </main>
  );
}

export default function VerifyPage() {
  const tCommon = useTranslations("common");
  return (
    <Suspense
      fallback={
        <main className="min-h-screen flex items-center justify-center px-4">
          <div className="text-center">
            <div className="relative w-20 h-20 mx-auto mb-6">
              <div className="absolute inset-0 bg-[var(--brand-pink)]/20 rounded-full animate-pulse" />
              <div className="relative w-full h-full rounded-full bg-[var(--brand-pink)]/10 flex items-center justify-center border border-[var(--brand-pink)]/30">
                <LoaderIcon className="w-10 h-10 text-[var(--brand-pink)] animate-spin" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-[var(--text)] mb-2">{tCommon("loading")}</h1>
          </div>
        </main>
      }
    >
      <VerifyContent />
    </Suspense>
  );
}
