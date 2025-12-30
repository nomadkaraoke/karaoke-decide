"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, setAuthToken } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui";
import { CheckIcon, XIcon, LoaderIcon } from "@/components/icons";

function VerifyContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { checkAuth } = useAuth();
  const [status, setStatus] = useState<"verifying" | "success" | "error">(
    "verifying"
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const verifyToken = async () => {
      const token = searchParams.get("token");

      if (!token) {
        setStatus("error");
        setError("No verification token found. Please request a new magic link.");
        return;
      }

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
        setError(
          err instanceof Error
            ? err.message
            : "Failed to verify token. It may have expired or already been used."
        );
      }
    };

    verifyToken();
  }, [searchParams, checkAuth, router]);

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
          Verification failed
        </h1>
        <p className="text-white/60 mb-6">{error}</p>

        <div className="space-y-3">
          <Link href="/login">
            <Button variant="primary" className="w-full">
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
