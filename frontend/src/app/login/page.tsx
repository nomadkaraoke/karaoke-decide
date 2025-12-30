"use client";

import { useState, useEffect, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Button, Input } from "@/components/ui";
import { MailIcon, CheckIcon, MicrophoneIcon } from "@/components/icons";

export default function LoginPage() {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Redirect if already authenticated
  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.push("/my-songs");
    }
  }, [isAuthenticated, isLoading, router]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      await api.auth.requestMagicLink(email);
      setIsSuccess(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to send magic link. Please try again."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  // Show loading while checking auth
  if (isLoading) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-[#ff2d92] border-t-transparent rounded-full animate-spin" />
      </main>
    );
  }

  // Success state
  if (isSuccess) {
    return (
      <main className="min-h-screen flex items-center justify-center px-4">
        <div className="w-full max-w-md">
          <div className="text-center">
            {/* Success icon */}
            <div className="relative w-20 h-20 mx-auto mb-6">
              <div className="absolute inset-0 bg-green-500/20 rounded-full animate-pulse" />
              <div className="relative w-full h-full rounded-full bg-green-500/10 flex items-center justify-center border border-green-500/30">
                <CheckIcon className="w-10 h-10 text-green-400" />
              </div>
            </div>

            <h1 className="text-2xl font-bold text-white mb-2">
              Check your email!
            </h1>
            <p className="text-white/60 mb-6">
              We sent a magic link to{" "}
              <span className="text-white font-medium">{email}</span>
            </p>
            <p className="text-white/40 text-sm mb-8">
              Click the link in your email to sign in. The link expires in 15
              minutes.
            </p>

            <div className="space-y-3">
              <Button
                variant="secondary"
                className="w-full"
                onClick={() => {
                  setIsSuccess(false);
                  setEmail("");
                }}
              >
                Use a different email
              </Button>
              <Link href="/">
                <Button variant="ghost" className="w-full">
                  Back to home
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="relative w-16 h-16 mx-auto mb-4">
            <MicrophoneIcon className="w-full h-full text-[#ff2d92]" />
            <div className="absolute inset-0 blur-xl bg-[#ff2d92]/30" />
          </div>
          <h1 className="text-2xl font-bold text-white mb-2">Welcome back</h1>
          <p className="text-white/60">
            Sign in to access your songs and recommendations
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            type="email"
            label="Email address"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={error || undefined}
            required
            autoFocus
            autoComplete="email"
          />

          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="w-full"
            isLoading={isSubmitting}
            leftIcon={<MailIcon className="w-5 h-5" />}
          >
            Send Magic Link
          </Button>
        </form>

        {/* Footer */}
        <div className="mt-8 text-center">
          <p className="text-white/40 text-sm">
            No password needed. We&apos;ll send you a secure link to sign in.
          </p>
        </div>

        {/* Divider */}
        <div className="my-8 flex items-center gap-4">
          <div className="flex-1 h-px bg-white/10" />
          <span className="text-white/30 text-xs uppercase">or</span>
          <div className="flex-1 h-px bg-white/10" />
        </div>

        {/* Alternative action */}
        <div className="text-center">
          <p className="text-white/60 text-sm mb-3">
            Just want to browse? No account needed.
          </p>
          <Link href="/">
            <Button variant="ghost">Browse Songs</Button>
          </Link>
        </div>
      </div>
    </main>
  );
}
