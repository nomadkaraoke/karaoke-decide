"use client";

import { useState, useEffect, FormEvent } from "react";
import { Link, useRouter } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import { useAuth } from "@/contexts/AuthContext";
import { getCurrentLocale } from "@/lib/api";
import { GOOGLE_CLIENT_ID, LOGIN_CODE_LENGTH } from "@/lib/constants";
import * as genAuth from "@/lib/genAuth";
import { Button, Input } from "@/components/ui";
import { GoogleSignInButton } from "@/components/GoogleSignInButton";
import { MailIcon, CheckIcon, MicrophoneIcon } from "@/components/icons";

/** Only allow same-site relative paths as post-login targets. */
function safeNext(raw: string | null): string {
  if (raw && raw.startsWith("/") && !raw.startsWith("//") && !raw.includes("\\")) {
    return raw;
  }
  return "/my-songs";
}

export default function LoginPage() {
  const router = useRouter();
  const t = useTranslations("auth");
  const { isAuthenticated, isLoading, login, checkAuth } = useAuth();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Read ?next= before the first render so an already-signed-in redirect honours it.
  const [next] = useState(() =>
    typeof window === "undefined"
      ? "/my-songs"
      : safeNext(new URLSearchParams(window.location.search).get("next"))
  );

  // Redirect once signed in (including when already signed in on arrival)
  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.replace(next);
    }
  }, [isAuthenticated, isLoading, next, router]);

  const errorMessage = (err: unknown, fallbackKey: "failedToSendCode" | "failedToVerifyCode") => {
    if (err instanceof genAuth.GenAuthError) {
      if (err.code === "invalid_code") return t("invalidCode");
      if (err.code === "expired") return t("codeExpired");
      if (err.code === "too_many_attempts") return t("tooManyAttempts");
    }
    return t(fallbackKey);
  };

  const requestCode = async () => {
    await genAuth.sendCode(email, getCurrentLocale());
  };

  const handleSendCode = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setIsSubmitting(true);
    try {
      await requestCode();
      setStep("code");
    } catch (err) {
      setError(errorMessage(err, "failedToSendCode"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const completeSignIn = async (sessionToken: string) => {
    await login(sessionToken);
    await checkAuth();
  };

  const handleVerify = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setIsSubmitting(true);
    try {
      await completeSignIn(await genAuth.verifyCode(email, code));
    } catch (err) {
      setError(errorMessage(err, "failedToVerifyCode"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleResend = async () => {
    setError(null);
    setNotice(null);
    setIsSubmitting(true);
    try {
      await requestCode();
      setCode("");
      setNotice(t("codeResent"));
    } catch (err) {
      setError(errorMessage(err, "failedToSendCode"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleGoogle = async (idToken: string) => {
    setError(null);
    setIsSubmitting(true);
    try {
      await completeSignIn(await genAuth.googleSignIn(idToken));
    } catch {
      setError(t("googleSignInFailed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-[var(--brand-pink)] border-t-transparent rounded-full animate-spin" />
      </main>
    );
  }

  if (step === "code") {
    return (
      <main className="min-h-screen flex items-center justify-center px-4">
        <div className="w-full max-w-md">
          <div className="text-center mb-8">
            <div className="relative w-16 h-16 mx-auto mb-4 rounded-full bg-green-500/10 flex items-center justify-center border border-green-500/30">
              <CheckIcon className="w-8 h-8 text-green-400" />
            </div>
            <h1 className="text-2xl font-bold text-[var(--text)] mb-2">{t("enterCode")}</h1>
            <p className="text-[var(--text)]/60">
              {t("codeSentTo")}{" "}
              <span className="text-[var(--text)] font-medium">{email}</span>
            </p>
          </div>

          <form onSubmit={handleVerify} className="space-y-4">
            <Input
              data-testid="login-code"
              type="text"
              inputMode="numeric"
              pattern={`[0-9]{${LOGIN_CODE_LENGTH}}`}
              maxLength={LOGIN_CODE_LENGTH}
              label={t("codeLabel")}
              placeholder={"0".repeat(LOGIN_CODE_LENGTH)}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              error={error || undefined}
              required
              autoFocus
              autoComplete="one-time-code"
            />
            {notice && <p className="text-sm text-green-400">{notice}</p>}

            <Button
              data-testid="login-verify"
              type="submit"
              variant="primary"
              size="lg"
              className="w-full"
              isLoading={isSubmitting}
              disabled={code.length !== LOGIN_CODE_LENGTH}
            >
              {t("verifyCode")}
            </Button>
          </form>

          <div className="mt-6 space-y-3">
            <Button
              data-testid="login-resend"
              variant="secondary"
              className="w-full"
              onClick={handleResend}
              disabled={isSubmitting}
            >
              {t("resendCode")}
            </Button>
            <Button
              variant="ghost"
              className="w-full"
              onClick={() => {
                setStep("email");
                setCode("");
                setError(null);
                setNotice(null);
              }}
            >
              {t("useDifferentEmail")}
            </Button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="relative w-16 h-16 mx-auto mb-4">
            <MicrophoneIcon className="w-full h-full text-[var(--brand-pink)]" />
            <div className="absolute inset-0 blur-xl bg-[var(--brand-pink)]/30" />
          </div>
          <h1 className="text-2xl font-bold text-[var(--text)] mb-2">{t("welcomeBack")}</h1>
          <p className="text-[var(--text)]/60">{t("signInToGetPicks")}</p>
        </div>

        <form onSubmit={handleSendCode} className="space-y-4">
          <Input
            data-testid="login-email"
            type="email"
            label={t("emailLabel")}
            placeholder={t("emailPlaceholder")}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={error || undefined}
            required
            autoFocus
            autoComplete="email"
          />

          <Button
            data-testid="login-send-code"
            type="submit"
            variant="primary"
            size="lg"
            className="w-full"
            isLoading={isSubmitting}
            leftIcon={<MailIcon className="w-5 h-5" />}
          >
            {t("sendCode")}
          </Button>
        </form>

        <GoogleSignInButtonSection onCredential={handleGoogle} onError={() => setError(t("googleSignInFailed"))} orLabel={t("or")} />

        <div className="mt-8 text-center">
          <p className="text-[var(--text)]/40 text-sm">{t("signInHint")}</p>
        </div>

        <div className="my-8 flex items-center gap-4">
          <div className="flex-1 h-px bg-[var(--card-border)]" />
          <span className="text-[var(--text)]/30 text-xs uppercase">{t("or")}</span>
          <div className="flex-1 h-px bg-[var(--card-border)]" />
        </div>

        <div className="text-center">
          <p className="text-[var(--text)]/60 text-sm mb-3">{t("justWantToBrowse")}</p>
          <Link href="/">
            <Button variant="ghost">{t("browseSongs")}</Button>
          </Link>
        </div>
      </div>
    </main>
  );
}

function GoogleSignInButtonSection({
  onCredential,
  onError,
  orLabel,
}: {
  onCredential: (idToken: string) => void;
  onError: () => void;
  orLabel: string;
}) {
  if (!GOOGLE_CLIENT_ID) return null;
  return (
    <>
      <div className="my-6 flex items-center gap-4">
        <div className="flex-1 h-px bg-[var(--card-border)]" />
        <span className="text-[var(--text)]/30 text-xs uppercase">{orLabel}</span>
        <div className="flex-1 h-px bg-[var(--card-border)]" />
      </div>
      <GoogleSignInButton onCredential={onCredential} onError={onError} />
    </>
  );
}
