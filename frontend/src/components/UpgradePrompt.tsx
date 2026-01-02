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
          <h2 className="text-xl font-bold text-white mb-2">Check Your Email</h2>
          <p className="text-white/70">
            We&apos;ve sent a verification link to <span className="text-white font-medium">{email}</span>.
            Click the link to verify your account and unlock all features.
          </p>
          <p className="text-white/50 text-sm mt-4">
            Your quiz results and progress will be saved when you verify.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto p-8 rounded-2xl bg-gradient-to-br from-[#ff2d92]/20 via-[#b347ff]/10 to-[#00f5ff]/10 border border-white/10">
      <div className="text-center mb-6">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-[#ff2d92] to-[#b347ff] flex items-center justify-center">
          <SparklesIcon className="w-8 h-8 text-white" />
        </div>
        <h2 className="text-xl font-bold text-white mb-2">{title}</h2>
        <p className="text-white/70">{description}</p>
        {featureName && (
          <p className="text-white/50 text-sm mt-2">
            <strong className="text-[#00f5ff]">{featureName}</strong> requires a verified account.
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

      <div className="mt-6 pt-6 border-t border-white/10">
        <h3 className="text-sm font-medium text-white/80 mb-3">With an account you can:</h3>
        <ul className="space-y-2 text-sm text-white/60">
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[#00f5ff]" />
            Connect Spotify and Last.fm
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[#00f5ff]" />
            Sync your listening history
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[#00f5ff]" />
            Save your progress across devices
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[#00f5ff]" />
            Create and manage playlists
          </li>
        </ul>
      </div>
    </div>
  );
}
