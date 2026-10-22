"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/routing";
import { Button } from "@/components/ui";
import { CheckIcon, SparklesIcon } from "@/components/icons";

interface UpgradePromptProps {
  title?: string;
  description?: string;
  featureName?: string;
}

export function UpgradePrompt({
  title,
  description,
  featureName,
}: UpgradePromptProps) {
  const t = useTranslations('components.upgrade');
  const resolvedTitle = title || t('defaultTitle');
  const resolvedDescription = description || t('defaultDescription');
  return (
    <div className="max-w-md mx-auto p-8 rounded-2xl bg-gradient-to-br from-[var(--brand-pink)]/20 via-[var(--brand-purple)]/10 to-[var(--brand-blue)]/10 border border-[var(--card-border)]">
      <div className="text-center mb-6">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-[var(--brand-pink)] to-[var(--brand-purple)] flex items-center justify-center">
          <SparklesIcon className="w-8 h-8 text-[var(--text)]" />
        </div>
        <h2 className="text-xl font-bold text-[var(--text)] mb-2">{resolvedTitle}</h2>
        <p className="text-[var(--text-muted)]">{resolvedDescription}</p>
        {featureName && (
          <p className="text-[var(--text-subtle)] text-sm mt-2">
            {t('requiresVerified', { feature: featureName })}
          </p>
        )}
      </div>

      <Link href="/login">
        <Button variant="primary" className="w-full">
          {t('createAccount')}
        </Button>
      </Link>

      <div className="mt-6 pt-6 border-t border-[var(--card-border)]">
        <h3 className="text-sm font-medium text-[var(--text-muted)] mb-3">{t('withAccountYouCan')}</h3>
        <ul className="space-y-2 text-sm text-[var(--text-muted)]">
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            {t('connectSpotifyLastfm')}
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            {t('syncListeningHistory')}
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            {t('saveProgressAcrossDevices')}
          </li>
          <li className="flex items-center gap-2">
            <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
            {t('createAndManagePlaylists')}
          </li>
        </ul>
      </div>
    </div>
  );
}
