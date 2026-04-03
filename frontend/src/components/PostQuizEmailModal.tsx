"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { MailIcon } from "@/components/icons";

interface PostQuizEmailModalProps {
  isOpen: boolean;
  onEmailSubmit: (email: string) => Promise<void>;
  isSubmitting: boolean;
}

/**
 * Modal shown after quiz submission to collect user's email.
 * User must enter email to see their recommendations.
 */
export function PostQuizEmailModal({
  isOpen,
  onEmailSubmit,
  isSubmitting,
}: PostQuizEmailModalProps) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const t = useTranslations('components.postQuizEmail');

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      setError(t('invalidEmail'));
      return;
    }

    try {
      await onEmailSubmit(email);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('failedToSaveEmail'));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />

      {/* Modal */}
      <div className="relative w-full max-w-md mx-4 bg-[var(--card)] rounded-2xl shadow-2xl border border-[var(--card-border)] overflow-hidden">
        {/* Header */}
        <div className="p-6 pb-4">
          <div className="flex items-center justify-center w-12 h-12 mx-auto mb-4 rounded-full bg-[var(--brand-pink)]/20">
            <MailIcon className="w-6 h-6 text-[var(--brand-pink)]" />
          </div>
          <h2 className="text-xl font-bold text-[var(--text)] text-center">
            {t('almostThere')}
          </h2>
          <p className="text-[var(--text-muted)] text-center mt-2">
            {t('enterEmailToSeeRecs')}
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="px-6 pb-6">
          <div className="space-y-4">
            <div>
              <label htmlFor="email" className="sr-only">
                {t('emailLabel')}
              </label>
              <input
                id="email"
                type="email"
                placeholder={t('emailPlaceholder')}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
                className={`
                  w-full px-4 py-3 rounded-lg
                  bg-[var(--secondary)] border
                  text-[var(--text)] placeholder-[var(--text-subtle)]
                  focus:outline-none focus:ring-2 focus:ring-[var(--brand-pink)]
                  transition-all duration-200
                  ${error ? "border-red-500" : "border-[var(--card-border)]"}
                  disabled:opacity-50
                `}
                autoComplete="email"
                autoFocus
              />
              {error && (
                <p className="mt-2 text-sm text-red-500">{error}</p>
              )}
            </div>

            <button
              type="submit"
              disabled={isSubmitting || !email.trim()}
              className={`
                w-full py-3 px-4 rounded-lg font-medium
                transition-all duration-200
                ${
                  email.trim()
                    ? "bg-[var(--brand-pink)] text-white hover:opacity-90"
                    : "bg-[var(--secondary)] text-[var(--text-muted)]"
                }
                disabled:opacity-50 disabled:cursor-not-allowed
              `}
            >
              {isSubmitting ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                  {t('saving')}
                </span>
              ) : (
                t('continue')
              )}
            </button>
          </div>

          <p className="mt-4 text-xs text-[var(--text-subtle)] text-center">
            {t('updateNote')}
            <br />
            {t('noSpam')}
          </p>
        </form>
      </div>
    </div>
  );
}
