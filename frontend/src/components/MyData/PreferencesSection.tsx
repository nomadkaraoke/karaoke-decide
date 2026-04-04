"use client";

import { useState, useCallback, useEffect } from "react";
import { Link } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { ChevronDownIcon, CheckIcon, SparklesIcon } from "@/components/icons";
import { Button, LoadingPulse } from "@/components/ui";

interface UserPreferences {
  decade_preference: string | null;
  energy_preference: "chill" | "medium" | "high" | null;
  genres: string[];
}

interface Props {
  isExpanded: boolean;
  onToggle: () => void;
}

const DECADES = ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s"];
const ENERGY_OPTIONS: {
  value: "chill" | "medium" | "high";
  labelKey: string;
  descKey: string;
}[] = [
  { value: "chill", labelKey: "chill", descKey: "chillDesc" },
  { value: "medium", labelKey: "medium", descKey: "mediumDesc" },
  { value: "high", labelKey: "highEnergy", descKey: "highEnergyDesc" },
];

const GENRES = [
  { id: "pop", key: "pop" },
  { id: "rock", key: "rock" },
  { id: "hiphop", key: "hiphop" },
  { id: "rnb", key: "rnb" },
  { id: "country", key: "country" },
  { id: "electronic", key: "electronic" },
  { id: "metal", key: "metal" },
  { id: "jazz", key: "jazz" },
  { id: "latin", key: "latin" },
  { id: "indie", key: "indie" },
  { id: "kpop", key: "kpop" },
  { id: "disco", key: "disco" },
  { id: "classic-rock", key: "classicRock" },
  { id: "musical", key: "musical" },
  { id: "reggae", key: "reggae" },
];

export function PreferencesSection({ isExpanded, onToggle }: Props) {
  const t = useTranslations('components.myDataPreferences');
  const tCommon = useTranslations('common');
  const [preferences, setPreferences] = useState<UserPreferences>({
    decade_preference: null,
    energy_preference: null,
    genres: [],
  });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Editing state
  const [editedPreferences, setEditedPreferences] =
    useState<UserPreferences | null>(null);

  const loadPreferences = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.my.getPreferences();
      setPreferences(response);
      setEditedPreferences(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t('failedToLoadPreferences')
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPreferences();
  }, [loadPreferences]);

  const hasChanges = editedPreferences !== null;

  const currentPrefs = editedPreferences || preferences;

  const handleDecadeChange = (decade: string | null) => {
    const newPrefs = {
      ...(editedPreferences || preferences),
      decade_preference: decade,
    };
    setEditedPreferences(newPrefs);
  };

  const handleEnergyChange = (energy: "chill" | "medium" | "high" | null) => {
    const newPrefs = {
      ...(editedPreferences || preferences),
      energy_preference: energy,
    };
    setEditedPreferences(newPrefs);
  };

  const handleGenreToggle = (genreId: string) => {
    const currentGenres = currentPrefs.genres;
    const newGenres = currentGenres.includes(genreId)
      ? currentGenres.filter((g) => g !== genreId)
      : [...currentGenres, genreId];
    const newPrefs = {
      ...(editedPreferences || preferences),
      genres: newGenres,
    };
    setEditedPreferences(newPrefs);
  };

  const handleSave = async () => {
    if (!editedPreferences) return;

    try {
      setIsSaving(true);
      setError(null);
      setSuccessMessage(null);

      await api.my.updatePreferences({
        decade_preference: editedPreferences.decade_preference,
        energy_preference: editedPreferences.energy_preference,
        genres: editedPreferences.genres,
      });

      // Refetch to ensure we have server state
      await loadPreferences();
      setSuccessMessage(t('preferencesSaved'));
    } catch (err) {
      setError(err instanceof Error ? err.message : t('failedToSavePreferences'));
    } finally {
      setIsSaving(false);
    }
  };

  // Clear success message after 3 seconds
  useEffect(() => {
    if (successMessage) {
      const timer = setTimeout(() => setSuccessMessage(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [successMessage]);

  const handleCancel = () => {
    setEditedPreferences(null);
  };

  const hasPreferences =
    preferences.decade_preference ||
    preferences.energy_preference ||
    preferences.genres.length > 0;

  return (
    <div className="rounded-2xl bg-[var(--card)] border border-[var(--card-border)] overflow-hidden">
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full p-5 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[var(--brand-blue)]/20 flex items-center justify-center">
            <SparklesIcon className="w-5 h-5 text-[var(--brand-blue)]" />
          </div>
          <div>
            <h2 className="font-semibold text-[var(--text)]">{t('title')}</h2>
            <p className="text-sm text-[var(--text-muted)]">
              {hasPreferences ? t('customized') : t('notSet')}
            </p>
          </div>
        </div>
        <ChevronDownIcon
          className={`w-5 h-5 text-[var(--text-muted)] transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* Content */}
      {isExpanded && (
        <div className="px-5 pb-5 space-y-6">
          {/* Error message */}
          {error && (
            <div className="p-3 rounded-xl bg-[var(--error)]/10 border border-[var(--error)]/30 text-[var(--error)] text-sm">
              {error}
            </div>
          )}

          {/* Success message */}
          {successMessage && (
            <div className="p-3 rounded-xl bg-[var(--success)]/10 border border-[var(--success)]/30 text-[var(--success)] text-sm">
              {successMessage}
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={3} />
          ) : (
            <>
              {/* Decade preference */}
              <div>
                <h3 className="text-sm font-medium text-[var(--text)] mb-3">
                  {t('favoriteDecade')}
                </h3>
                <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
                  {DECADES.map((decade) => (
                    <button
                      key={decade}
                      onClick={() =>
                        handleDecadeChange(
                          currentPrefs.decade_preference === decade ? null : decade
                        )
                      }
                      className={`
                        p-2 rounded-lg text-sm transition-all
                        ${
                          currentPrefs.decade_preference === decade
                            ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border text-[var(--text)]"
                            : "bg-[var(--secondary)] border border-[var(--card-border)] text-[var(--text-muted)] hover:border-[var(--text-subtle)]"
                        }
                      `}
                    >
                      {decade}
                    </button>
                  ))}
                </div>
              </div>

              {/* Energy preference */}
              <div>
                <h3 className="text-sm font-medium text-[var(--text)] mb-3">
                  {t('energyLevel')}
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {ENERGY_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      onClick={() =>
                        handleEnergyChange(
                          currentPrefs.energy_preference === option.value
                            ? null
                            : option.value
                        )
                      }
                      className={`
                        p-3 rounded-lg text-left transition-all
                        ${
                          currentPrefs.energy_preference === option.value
                            ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border"
                            : "bg-[var(--secondary)] border border-[var(--card-border)] hover:border-[var(--text-subtle)]"
                        }
                      `}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-[var(--text)]">
                          {t(option.labelKey)}
                        </span>
                        {currentPrefs.energy_preference === option.value && (
                          <CheckIcon className="w-4 h-4 text-[var(--brand-pink)]" />
                        )}
                      </div>
                      <p className="text-xs text-[var(--text-subtle)] mt-1">
                        {t(option.descKey)}
                      </p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Genre preferences */}
              <div>
                <h3 className="text-sm font-medium text-[var(--text)] mb-3">
                  {t('favoriteGenres')}
                </h3>
                <div className="flex flex-wrap gap-2">
                  {GENRES.map((genre) => (
                    <button
                      key={genre.id}
                      onClick={() => handleGenreToggle(genre.id)}
                      className={`
                        px-3 py-1.5 rounded-full text-sm transition-all
                        ${
                          currentPrefs.genres.includes(genre.id)
                            ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border text-[var(--text)]"
                            : "bg-[var(--secondary)] border border-[var(--card-border)] text-[var(--text-muted)] hover:border-[var(--text-subtle)]"
                        }
                      `}
                    >
                      {t(`genreLabels.${genre.key}`)}
                      {currentPrefs.genres.includes(genre.id) && (
                        <CheckIcon className="w-3 h-3 ms-1 inline" />
                      )}
                    </button>
                  ))}
                </div>
              </div>

              {/* Save/Cancel buttons */}
              {hasChanges && (
                <div className="flex gap-2 pt-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleCancel}
                    disabled={isSaving}
                  >
                    {tCommon('cancel')}
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleSave}
                    isLoading={isSaving}
                  >
                    {tCommon('saveChanges')}
                  </Button>
                </div>
              )}

              {/* Retake quiz link */}
              <div className="pt-2 border-t border-[var(--card-border)]">
                <Link
                  href="/quiz"
                  className="text-sm text-[var(--brand-blue)] hover:underline flex items-center gap-2"
                >
                  <SparklesIcon className="w-4 h-4" />
                  {t('retakeQuiz')}
                </Link>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
