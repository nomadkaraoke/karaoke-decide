"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { XIcon, SpotifyIcon } from "@/components/icons";
import { Button } from "@/components/ui";

function safeDecodeURIComponent(str: string): string {
  try {
    return decodeURIComponent(str);
  } catch {
    return str;
  }
}

function ErrorContent() {
  const searchParams = useSearchParams();
  const message = searchParams.get("message") || "An unknown error occurred";

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md text-center">
        <div className="relative w-20 h-20 mx-auto mb-6">
          <div className="absolute inset-0 bg-red-500/20 rounded-full" />
          <div className="relative w-full h-full rounded-full bg-red-500/10 flex items-center justify-center border border-red-500/30">
            <SpotifyIcon className="w-8 h-8 text-[#1DB954]" />
          </div>
          <div className="absolute -bottom-1 -right-1 w-8 h-8 rounded-full bg-red-500 flex items-center justify-center">
            <XIcon className="w-5 h-5 text-[var(--text)]" />
          </div>
        </div>

        <h1 className="text-2xl font-bold text-[var(--text)] mb-2">
          Connection Failed
        </h1>
        <p className="text-[var(--text-muted)] mb-6">{safeDecodeURIComponent(message)}</p>

        <div className="space-y-3">
          <Link href="/services">
            <Button variant="primary" className="w-full">
              Try Again
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

export default function SpotifyErrorPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen flex items-center justify-center">
          <div className="w-8 h-8 border-2 border-[var(--brand-pink)] border-t-transparent rounded-full animate-spin" />
        </main>
      }
    >
      <ErrorContent />
    </Suspense>
  );
}
