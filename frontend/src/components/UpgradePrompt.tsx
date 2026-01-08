"use client";

import { useState, FormEvent } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { Button, Input } from "@/components/ui";
import { CheckIcon, SparklesIcon } from "@/components/icons";

interface UpgradePromptProps {
  title?: string;
  description?: string;
  featureName?: string;
}

export function UpgradePrompt({
  title = "Create Your Account",
  description = "Verify your email to unlock this feature and save your progress.",
  featureName,
}: UpgradePromptProps) {
  const { requestUpgrade } = useAuth();
  const [email, setEmail] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;

    setIsSubmitting(true);
    setError(null);

    try {
      await requestUpgrade(email);
      setSuccess(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to send verification email"
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  if (success) {
    return (
      <div className="max-w-md mx-auto p-8 rounded-2xl bg-gradient-to-br from-green-500/20 to-emerald-500/10 border border-green-500/30">
        <div className="text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-green-500/20 flex items-center justify-center">
            <CheckIcon className="w-8 h-8 text-green-400" />
          </div>
          <h2 className="text-xl font-bold text-[var(--text)] mb-2">Check Your Email</h2>
          <p className="text-[var(--text-muted)]">
            We&apos;ve sent a verification link to <span className="text-[var(--text)] font-medium">{email}</span>.
            Click the link to verify your account and unlock all features.
          </p>
          <p className="text-[var(--text-subtle)] text-sm mt-4">
            Your quiz results and progress will be saved when you verify.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto p-8 rounded-2xl bg-gradient-to-br from-[var(--brand-pink)]/20 via-[var(--brand-purple)]/10 to-[var(--brand-blue)]/10 border border-[var(--card-border)]">
      <div className="text-center mb-6">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-[var(--brand-pink)] to-[var(--brand-purple)] flex items-center justify-center">
          <SparklesIcon className="w-8 h-8 text-[var(--text)]" />
        </div>
        <h2 className="text-xl font-bold text-[var(--text)] mb-2">{title}</h2>
        <p className="text-[var(--text-muted)]">{description}</p>
        {featureName && (
          <p className="text-[var(--text-subtle)] text-sm mt-2">
            <strong className="text-[var(--brand-pink)]">{featureName}</strong> requires a verified account.
          </p>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <Input
          type="email"
          placeholder="Enter your email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={error || undefined}
          required
        />
        <Button
          type="submit"
          variant="primary"
          className="w-full"
          isLoading={isSubmitting}
          disabled={!email.trim()}
        >
          Send Verification Link
        </Button>
      </form>

      <div className="mt-6 pt-6 border-t border-[var(--card-border)]">
        <h3 className="text-sm font-medium text-[var(--text-muted)] mb-3">With an account you can:</h3>
        <ul className="space-y-2 text-sm text-[var(--text-muted)]">
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            Connect Spotify and Last.fm
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            Sync your listening history
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            Save your progress across devices
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            Create and manage playlists
          </li>
        </ul>
      </div>
    </div>
  );
}
