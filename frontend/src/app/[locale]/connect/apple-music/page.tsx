"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/routing";
import { api, getAuthToken } from "@/lib/api";
import { Button } from "@/components/ui";
import { AppleMusicIcon, CheckIcon } from "@/components/icons";
import { APP_RETURN_URL, loadMusicKit } from "@/lib/musickit";

type Phase =
  | "loading"
  | "ready"
  | "connecting"
  | "done"
  | "signedOut"
  | "expired"
  | "refused"
  | "unavailable"
  | "cancelled"
  | "error";

/**
 * Connect Apple Music: MusicKit JS signs the user in to Apple Music, and decide stores the
 * Music User Token and reads their library. Opened from My Data (signed in) or from the app,
 * which passes a one-time `state` and `return=app` because its browser isn't signed in here.
 */
function ConnectAppleMusic() {
  const t = useTranslations("appleMusicConnect");
  const params = useSearchParams();
  const state = params.get("state");
  const fromApp = params.get("return") === "app";
  const [phase, setPhase] = useState<Phase>("loading");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!state && !getAuthToken()) {
        setPhase("signedOut");
        return;
      }
      try {
        const { developer_token } = await api.services.appleMusicDeveloperToken();
        await loadMusicKit(developer_token);
        if (!cancelled) setPhase("ready");
      } catch {
        if (!cancelled) setPhase("unavailable");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [state]);

  const connect = useCallback(async () => {
    setPhase("connecting");
    let musicUserToken: string | undefined;
    try {
      musicUserToken = await window.MusicKit!.getInstance().authorize();
    } catch {
      setPhase("cancelled");
      return;
    }
    if (!musicUserToken) {
      setPhase("cancelled");
      return;
    }
    const result = await api.services.connectAppleMusic(musicUserToken, state);
    if (result === "ok") {
      setPhase("done");
      if (fromApp) window.location.href = APP_RETURN_URL;
      return;
    }
    setPhase(
      result === "unauthorized" ? (state ? "expired" : "signedOut") : result === "refused" ? "refused" : result,
    );
  }, [state, fromApp]);

  const message: Partial<Record<Phase, string>> = {
    signedOut: t("signedOut"),
    expired: t("expired"),
    refused: t("refused"),
    unavailable: t("unavailable"),
    cancelled: t("cancelled"),
    error: t("error"),
  };

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md text-center space-y-5">
        <div className="relative w-20 h-20 mx-auto">
          <div className="w-full h-full rounded-full bg-[#FA243C]/10 flex items-center justify-center border border-[#FA243C]/30">
            <AppleMusicIcon className="w-8 h-8 text-[#FA243C]" />
          </div>
          {phase === "done" && (
            <div className="absolute -bottom-1 -right-1 w-8 h-8 rounded-full bg-green-500 flex items-center justify-center">
              <CheckIcon className="w-5 h-5 text-white" />
            </div>
          )}
        </div>

        <h1 className="text-2xl font-bold text-[var(--text)]">
          {phase === "done" ? t("connectedTitle") : t("title")}
        </h1>

        {phase === "done" ? (
          <>
            <p className="text-[var(--text-muted)]" data-testid="apple-music-done">
              {fromApp ? t("doneApp") : t("doneWeb")}
            </p>
            {fromApp ? (
              <Button variant="primary" onClick={() => (window.location.href = APP_RETURN_URL)}>
                {t("backToApp")}
              </Button>
            ) : (
              <Link href="/recommendations" className="inline-block text-[var(--brand-blue)] underline">
                {t("seePicks")}
              </Link>
            )}
          </>
        ) : (
          <>
            <p className="text-[var(--text-muted)]">{t("intro")}</p>
            {message[phase] && (
              <p
                className="p-3 rounded-xl bg-[var(--error)]/10 border border-[var(--error)]/30 text-[var(--error)] text-sm"
                data-testid="apple-music-error"
              >
                {message[phase]}
              </p>
            )}
            {phase === "signedOut" && !state ? (
              <Link href="/login" className="inline-block text-[var(--brand-blue)] underline">
                {t("signIn")}
              </Link>
            ) : phase === "expired" || phase === "unavailable" ? null : (
              <Button
                variant="primary"
                onClick={connect}
                isLoading={phase === "loading" || phase === "connecting"}
                disabled={phase === "loading" || phase === "connecting"}
                data-testid="apple-music-connect"
              >
                {t("connect")}
              </Button>
            )}
            <p className="text-xs text-[var(--text-subtle)]">{t("privacy")}</p>
          </>
        )}
      </div>
    </main>
  );
}

export default function ConnectAppleMusicPage() {
  return (
    <Suspense>
      <ConnectAppleMusic />
    </Suspense>
  );
}
