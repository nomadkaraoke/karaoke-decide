"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { GOOGLE_CLIENT_ID } from "@/lib/constants";

const GSI_SRC = "https://accounts.google.com/gsi/client";

interface GoogleIdentity {
  accounts: {
    id: {
      initialize: (config: {
        client_id: string;
        callback: (response: { credential?: string }) => void;
      }) => void;
      renderButton: (el: HTMLElement, options: Record<string, unknown>) => void;
    };
  };
}

declare global {
  interface Window {
    google?: GoogleIdentity;
  }
}

function loadGsi(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.google?.accounts?.id) return resolve();
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${GSI_SRC}"]`
    );
    const script = existing ?? document.createElement("script");
    script.addEventListener("load", () => resolve());
    script.addEventListener("error", () => reject(new Error("gsi load failed")));
    if (!existing) {
      script.src = GSI_SRC;
      script.async = true;
      document.head.appendChild(script);
    }
  });
}

interface GoogleSignInButtonProps {
  onCredential: (idToken: string) => void;
  onError?: () => void;
}

/** Renders Google's button; renders nothing unless NEXT_PUBLIC_GOOGLE_CLIENT_ID is set. */
export function GoogleSignInButton({ onCredential, onError }: GoogleSignInButtonProps) {
  const t = useTranslations("auth");
  const containerRef = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onCredential, onError });
  useEffect(() => {
    callbacks.current = { onCredential, onError };
  });

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;
    let cancelled = false;
    loadGsi()
      .then(() => {
        if (cancelled || !containerRef.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (response) => {
            if (response.credential) callbacks.current.onCredential(response.credential);
            else callbacks.current.onError?.();
          },
        });
        window.google.accounts.id.renderButton(containerRef.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "continue_with",
          width: 320,
        });
      })
      .catch(() => callbacks.current.onError?.());
    return () => {
      cancelled = true;
    };
  }, []);

  if (!GOOGLE_CLIENT_ID) return null;
  return (
    <div
      ref={containerRef}
      data-testid="login-google"
      className="flex justify-center"
      aria-label={t("continueWithGoogle")}
    />
  );
}
