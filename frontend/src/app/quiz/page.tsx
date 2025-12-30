"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { ProtectedPage } from "@/components/ProtectedPage";
import { QuizSongCard } from "@/components/QuizSongCard";
import { SparklesIcon, CheckIcon, ChevronRightIcon } from "@/components/icons";
import { Button, LoadingPulse } from "@/components/ui";

interface QuizSong {
  id: string;
  artist: string;
  title: string;
  decade: string;
  popularity: number;
  brand_count: number;
}

type EnergyPreference = "chill" | "medium" | "high" | null;

const DECADES = ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s"];
const ENERGY_OPTIONS: { value: EnergyPreference; label: string; description: string }[] = [
  { value: "chill", label: "Chill", description: "Slow ballads, easy listening" },
  { value: "medium", label: "Medium", description: "Classic sing-alongs" },
  { value: "high", label: "High Energy", description: "Dance hits, rock anthems" },
];

export default function QuizPage() {
  const router = useRouter();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [quizSongs, setQuizSongs] = useState<QuizSong[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // User selections
  const [selectedSongIds, setSelectedSongIds] = useState<Set<string>>(new Set());
  const [decadePreference, setDecadePreference] = useState<string | null>(null);
  const [energyPreference, setEnergyPreference] = useState<EnergyPreference>(null);

  // Submit state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{
    songs_added: number;
    recommendations_ready: boolean;
  } | null>(null);

  const loadQuizSongs = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.quiz.getSongs(15);
      setQuizSongs(response.songs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load quiz");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQuizSongs();
  }, [loadQuizSongs]);

  const toggleSong = (songId: string) => {
    setSelectedSongIds((prev) => {
      const next = new Set(prev);
      if (next.has(songId)) {
        next.delete(songId);
      } else {
        next.add(songId);
      }
      return next;
    });
  };

  const handleSubmit = async () => {
    try {
      setIsSubmitting(true);
      const response = await api.quiz.submit({
        known_song_ids: Array.from(selectedSongIds),
        decade_preference: decadePreference,
        energy_preference: energyPreference,
      });
      setSubmitResult(response);
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit quiz");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Progress indicator */}
          <div className="flex items-center justify-center gap-2 mb-8">
            {[1, 2, 3].map((s) => (
              <div
                key={s}
                className={`w-3 h-3 rounded-full transition-colors ${
                  s === step
                    ? "bg-[#ff2d92]"
                    : s < step
                    ? "bg-[#ff2d92]/50"
                    : "bg-white/20"
                }`}
              />
            ))}
          </div>

          {/* Step 1: Song Selection */}
          {step === 1 && (
            <>
              <div className="text-center mb-8">
                <h1 className="text-2xl font-bold text-white mb-2">
                  Which songs do you know?
                </h1>
                <p className="text-white/60">
                  Select the songs you recognize. This helps us understand your
                  music taste.
                </p>
              </div>

              {isLoading ? (
                <LoadingPulse count={6} />
              ) : error ? (
                <div className="text-center py-8">
                  <p className="text-white/60 mb-4">{error}</p>
                  <Button onClick={loadQuizSongs} variant="secondary">
                    Try again
                  </Button>
                </div>
              ) : (
                <>
                  {/* Selection count */}
                  <div className="flex items-center justify-between mb-4">
                    <span className="text-white/60 text-sm">
                      {selectedSongIds.size} of {quizSongs.length} selected
                    </span>
                    {selectedSongIds.size > 0 && (
                      <button
                        onClick={() => setSelectedSongIds(new Set())}
                        className="text-sm text-white/40 hover:text-white transition-colors"
                      >
                        Clear all
                      </button>
                    )}
                  </div>

                  {/* Song grid */}
                  <div className="grid grid-cols-1 gap-3 mb-8">
                    {quizSongs.map((song, index) => (
                      <QuizSongCard
                        key={song.id}
                        song={song}
                        isSelected={selectedSongIds.has(song.id)}
                        onToggle={() => toggleSong(song.id)}
                        index={index}
                      />
                    ))}
                  </div>

                  {/* Next button */}
                  <div className="sticky bottom-4">
                    <Button
                      variant="primary"
                      size="lg"
                      className="w-full"
                      onClick={() => setStep(2)}
                      rightIcon={<ChevronRightIcon className="w-5 h-5" />}
                    >
                      Continue
                    </Button>
                  </div>
                </>
              )}
            </>
          )}

          {/* Step 2: Preferences */}
          {step === 2 && (
            <>
              <div className="text-center mb-8">
                <h1 className="text-2xl font-bold text-white mb-2">
                  Your preferences
                </h1>
                <p className="text-white/60">
                  Optional: Help us fine-tune your recommendations.
                </p>
              </div>

              {/* Decade preference */}
              <div className="mb-8">
                <h2 className="text-lg font-semibold text-white mb-3">
                  Favorite decade?
                </h2>
                <div className="flex flex-wrap gap-2">
                  {DECADES.map((d) => (
                    <button
                      key={d}
                      onClick={() =>
                        setDecadePreference(decadePreference === d ? null : d)
                      }
                      className={`
                        px-4 py-2 rounded-full text-sm font-medium transition-all
                        ${
                          decadePreference === d
                            ? "bg-[#ff2d92] text-white"
                            : "bg-white/10 text-white/70 hover:bg-white/20"
                        }
                      `}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>

              {/* Energy preference */}
              <div className="mb-8">
                <h2 className="text-lg font-semibold text-white mb-3">
                  What energy level?
                </h2>
                <div className="grid grid-cols-1 gap-3">
                  {ENERGY_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      onClick={() =>
                        setEnergyPreference(
                          energyPreference === option.value ? null : option.value
                        )
                      }
                      className={`
                        p-4 rounded-xl text-left transition-all
                        ${
                          energyPreference === option.value
                            ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 border"
                            : "bg-white/5 border border-white/10 hover:border-white/20"
                        }
                      `}
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <h3 className="font-semibold text-white">
                            {option.label}
                          </h3>
                          <p className="text-sm text-white/60">
                            {option.description}
                          </p>
                        </div>
                        {energyPreference === option.value && (
                          <CheckIcon className="w-5 h-5 text-[#ff2d92]" />
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              {/* Navigation */}
              <div className="flex gap-3 sticky bottom-4">
                <Button
                  variant="secondary"
                  size="lg"
                  className="flex-1"
                  onClick={() => setStep(1)}
                >
                  Back
                </Button>
                <Button
                  variant="primary"
                  size="lg"
                  className="flex-1"
                  onClick={handleSubmit}
                  isLoading={isSubmitting}
                >
                  Finish Quiz
                </Button>
              </div>
            </>
          )}

          {/* Step 3: Results */}
          {step === 3 && submitResult && (
            <div className="text-center py-12">
              <div className="relative w-20 h-20 mx-auto mb-6">
                <div className="absolute inset-0 bg-green-500/20 rounded-full animate-pulse" />
                <div className="relative w-full h-full rounded-full bg-green-500/10 flex items-center justify-center border border-green-500/30">
                  <CheckIcon className="w-10 h-10 text-green-400" />
                </div>
              </div>

              <h1 className="text-2xl font-bold text-white mb-2">
                Quiz Complete!
              </h1>
              <p className="text-white/60 mb-8">
                We added {submitResult.songs_added} songs to your library.
                {submitResult.recommendations_ready &&
                  " Your personalized recommendations are ready!"}
              </p>

              <div className="space-y-3">
                <Button
                  variant="primary"
                  size="lg"
                  className="w-full"
                  onClick={() => router.push("/recommendations")}
                  leftIcon={<SparklesIcon className="w-5 h-5" />}
                >
                  View Recommendations
                </Button>
                <Button
                  variant="secondary"
                  size="lg"
                  className="w-full"
                  onClick={() => router.push("/my-songs")}
                >
                  View My Songs
                </Button>
              </div>
            </div>
          )}
        </div>
      </main>
    </ProtectedPage>
  );
}
