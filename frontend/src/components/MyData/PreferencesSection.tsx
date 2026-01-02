"use client";

import { useState, useCallback, useEffect } from "react";
import Link from "next/link";
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
  label: string;
  description: string;
}[] = [
  { value: "chill", label: "Chill", description: "Slow ballads, easy listening" },
  { value: "medium", label: "Medium", description: "Classic sing-alongs" },
  { value: "high", label: "High Energy", description: "Dance hits, rock anthems" },
];

const GENRES = [
  { id: "pop", label: "Pop" },
  { id: "rock", label: "Rock" },
  { id: "hiphop", label: "Hip-Hop" },
  { id: "rnb", label: "R&B" },
  { id: "country", label: "Country" },
  { id: "electronic", label: "Electronic" },
  { id: "metal", label: "Metal" },
  { id: "jazz", label: "Jazz" },
  { id: "latin", label: "Latin" },
  { id: "indie", label: "Indie" },
  { id: "kpop", label: "K-Pop" },
  { id: "disco", label: "Disco" },
  { id: "classic-rock", label: "Classic Rock" },
  { id: "musical", label: "Broadway" },
  { id: "reggae", label: "Reggae" },
];

export function PreferencesSection({ isExpanded, onToggle }: Props) {
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
        err instanceof Error ? err.message : "Failed to load preferences"
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
      setSuccessMessage("Preferences saved! Changes will apply to your next recommendations.");

      // Clear success message after 3 seconds
      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save preferences");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    setEditedPreferences(null);
  };

  const hasPreferences =
    preferences.decade_preference ||
    preferences.energy_preference ||
    preferences.genres.length > 0;

  return (
    <div className="rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 overflow-hidden">
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full p-5 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#00f5ff]/20 flex items-center justify-center">
            <SparklesIcon className="w-5 h-5 text-[#00f5ff]" />
          </div>
          <div>
            <h2 className="font-semibold text-white">Preferences</h2>
            <p className="text-sm text-white/60">
              {hasPreferences ? "Customized" : "Not set"}
            </p>
          </div>
        </div>
        <ChevronDownIcon
          className={`w-5 h-5 text-white/60 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* Content */}
      {isExpanded && (
        <div className="px-5 pb-5 space-y-6">
          {/* Error message */}
          {error && (
            <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Success message */}
          {successMessage && (
            <div className="p-3 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400 text-sm">
              {successMessage}
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={3} />
          ) : (
            <>
              {/* Decade preference */}
              <div>
                <h3 className="text-sm font-medium text-white mb-3">
                  Favorite decade
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
                            ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 border text-white"
                            : "bg-white/5 border border-white/10 text-white/70 hover:border-white/20"
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
                <h3 className="text-sm font-medium text-white mb-3">
                  Energy level
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
                            ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 border"
                            : "bg-white/5 border border-white/10 hover:border-white/20"
                        }
                      `}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium text-white">
                          {option.label}
                        </span>
                        {currentPrefs.energy_preference === option.value && (
                          <CheckIcon className="w-4 h-4 text-[#ff2d92]" />
                        )}
                      </div>
                      <p className="text-xs text-white/50 mt-1">
                        {option.description}
                      </p>
                    </button>
                  ))}
                </div>
              </div>

              {/* Genre preferences */}
              <div>
                <h3 className="text-sm font-medium text-white mb-3">
                  Favorite genres
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
                            ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 border text-white"
                            : "bg-white/5 border border-white/10 text-white/70 hover:border-white/20"
                        }
                      `}
                    >
                      {genre.label}
                      {currentPrefs.genres.includes(genre.id) && (
                        <CheckIcon className="w-3 h-3 ml-1 inline" />
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
                    Cancel
                  </Button>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleSave}
                    isLoading={isSaving}
                  >
                    Save Changes
                  </Button>
                </div>
              )}

              {/* Retake quiz link */}
              <div className="pt-2 border-t border-white/10">
                <Link
                  href="/quiz"
                  className="text-sm text-[#00f5ff] hover:underline flex items-center gap-2"
                >
                  <SparklesIcon className="w-4 h-4" />
                  Retake the quiz to update your preferences
                </Link>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
