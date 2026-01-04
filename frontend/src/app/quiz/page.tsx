"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { QuizArtistCard } from "@/components/QuizArtistCard";
import { SparklesIcon, CheckIcon, ChevronRightIcon, RefreshIcon } from "@/components/icons";
import { Button, LoadingPulse, LoadingOverlay } from "@/components/ui";

interface QuizArtist {
  name: string;
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;
  genres?: string[];
  image_url: string | null;
}

type EnergyPreference = "chill" | "medium" | "high" | null;

const DECADES = ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s"];
const ENERGY_OPTIONS: { value: EnergyPreference; label: string; emoji: string }[] = [
  { value: "chill", label: "Chill", emoji: "🎵" },
  { value: "medium", label: "Medium", emoji: "🎶" },
  { value: "high", label: "High Energy", emoji: "🔥" },
];

interface Genre {
  id: string;
  label: string;
  exampleArtists: string[];
  emoji: string;
}

const GENRES: Genre[] = [
  { id: "pop", label: "Pop", exampleArtists: ["Taylor Swift", "Ed Sheeran", "Dua Lipa"], emoji: "🎤" },
  { id: "rock", label: "Rock", exampleArtists: ["Queen", "Bon Jovi", "Foo Fighters"], emoji: "🎸" },
  { id: "hiphop", label: "Hip-Hop / Rap", exampleArtists: ["Drake", "Eminem", "Kendrick Lamar"], emoji: "🎧" },
  { id: "rnb", label: "R&B / Soul", exampleArtists: ["Beyoncé", "Usher", "Alicia Keys"], emoji: "🎹" },
  { id: "country", label: "Country", exampleArtists: ["Luke Combs", "Carrie Underwood", "Dolly Parton"], emoji: "🤠" },
  { id: "electronic", label: "Electronic / Dance", exampleArtists: ["Daft Punk", "Calvin Harris", "The Chainsmokers"], emoji: "🎛️" },
  { id: "metal", label: "Metal / Hard Rock", exampleArtists: ["Metallica", "AC/DC", "Iron Maiden"], emoji: "🤘" },
  { id: "jazz", label: "Jazz / Standards", exampleArtists: ["Frank Sinatra", "Ella Fitzgerald", "Michael Bublé"], emoji: "🎺" },
  { id: "latin", label: "Latin", exampleArtists: ["Bad Bunny", "Shakira", "J Balvin"], emoji: "💃" },
  { id: "indie", label: "Indie / Alternative", exampleArtists: ["Arctic Monkeys", "The 1975", "Tame Impala"], emoji: "🌙" },
  { id: "kpop", label: "K-Pop", exampleArtists: ["BTS", "BLACKPINK", "Stray Kids"], emoji: "🇰🇷" },
  { id: "disco", label: "Disco / Funk", exampleArtists: ["ABBA", "Earth, Wind & Fire", "Bee Gees"], emoji: "🕺" },
  { id: "classic-rock", label: "Classic Rock", exampleArtists: ["Journey", "Eagles", "Fleetwood Mac"], emoji: "🎷" },
  { id: "musical", label: "Broadway / Musical", exampleArtists: ["Wicked", "Les Misérables", "Hamilton"], emoji: "🎭" },
  { id: "reggae", label: "Reggae / Ska", exampleArtists: ["Bob Marley", "UB40", "Sean Paul"], emoji: "🇯🇲" },
];

// Streamlined 3-step quiz:
// Step 1: Genres (required)
// Step 2: Preferences - combined decade + energy (optional)
// Step 3: Artists (optional)
type QuizStep = 1 | 2 | 3;

export default function QuizPage() {
  const router = useRouter();
  const {
    isAuthenticated,
    isLoading: authLoading,
    hasCompletedQuiz,
    quizStatusLoading,
    startGuestSession,
    refreshQuizStatus,
  } = useAuth();
  const [step, setStep] = useState<QuizStep>(1);
  const [quizArtists, setQuizArtists] = useState<QuizArtist[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // User selections
  const [selectedGenres, setSelectedGenres] = useState<Set<string>>(new Set());
  const [selectedArtists, setSelectedArtists] = useState<Set<string>>(new Set());
  const [decadePreference, setDecadePreference] = useState<string | null>(null);
  const [energyPreference, setEnergyPreference] = useState<EnergyPreference>(null);

  // Submit state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  const loadQuizArtists = useCallback(async (genres?: string[]) => {
    try {
      setIsLoading(true);
      setError(null);
      // Load only 10 artists for a faster quiz
      const response = await api.quiz.getArtists(10, genres);
      setQuizArtists(response.artists);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load quiz");
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Create guest session if not authenticated
  useEffect(() => {
    const initSession = async () => {
      if (!authLoading && !isAuthenticated) {
        try {
          await startGuestSession();
        } catch (err) {
          console.error("Failed to create guest session:", err);
          setError("Failed to start quiz. Please try again.");
        }
      }
    };
    initSession();
  }, [authLoading, isAuthenticated, startGuestSession]);

  // Redirect users who have already completed the quiz
  useEffect(() => {
    if (!authLoading && !quizStatusLoading && isAuthenticated && hasCompletedQuiz) {
      router.push("/recommendations");
    }
  }, [authLoading, quizStatusLoading, isAuthenticated, hasCompletedQuiz, router]);

  // Load quiz artists when entering step 3
  useEffect(() => {
    if (isAuthenticated && step === 3) {
      const genresArray = selectedGenres.size > 0 ? Array.from(selectedGenres) : undefined;
      loadQuizArtists(genresArray);
    }
  }, [isAuthenticated, step, selectedGenres, loadQuizArtists]);

  const toggleArtist = (artistName: string) => {
    setSelectedArtists((prev) => {
      const next = new Set(prev);
      if (next.has(artistName)) {
        next.delete(artistName);
      } else {
        next.add(artistName);
      }
      return next;
    });
  };

  const toggleGenre = (genreId: string) => {
    setSelectedGenres((prev) => {
      const next = new Set(prev);
      if (next.has(genreId)) {
        next.delete(genreId);
      } else {
        next.add(genreId);
      }
      return next;
    });
  };

  const handleLoadMoreArtists = async () => {
    try {
      setIsLoadingMore(true);
      const currentArtistNames = quizArtists.map((a) => a.name);
      const genresArray = selectedGenres.size > 0 ? Array.from(selectedGenres) : undefined;
      const response = await api.quiz.getArtists(10, genresArray, currentArtistNames);

      const existingNames = new Set(quizArtists.map((a) => a.name));
      const newArtists = response.artists.filter((a) => !existingNames.has(a.name));

      if (newArtists.length > 0) {
        setQuizArtists((prev) => [...prev, ...newArtists]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more artists");
    } finally {
      setIsLoadingMore(false);
    }
  };

  const handleSubmit = async () => {
    try {
      setIsSubmitting(true);
      await api.quiz.submit({
        known_artists: Array.from(selectedArtists),
        decade_preference: decadePreference,
        energy_preference: energyPreference,
      });
      // Refresh quiz status so other components know quiz is completed
      await refreshQuizStatus();
      // Go to recommendations
      router.push("/recommendations");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit quiz");
      setIsSubmitting(false);
    }
  };

  const handleSkipToRecommendations = async () => {
    // Submit with current selections and go to recommendations
    await handleSubmit();
  };

  // Show loading while auth is being checked or redirecting
  if (authLoading || quizStatusLoading || !isAuthenticated || hasCompletedQuiz) {
    return <LoadingOverlay message="Starting quiz..." />;
  }

  return (
    <main className="min-h-screen pb-safe">
      <div className="max-w-2xl mx-auto px-4 py-6">
        {/* Progress indicator */}
        <div data-testid="progress-indicator" className="flex items-center justify-center gap-2 mb-8">
          {[1, 2, 3].map((s) => (
            <div
              key={s}
              data-testid={`progress-dot-${s}`}
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

        {/* Step 1: Genre Selection */}
        {step === 1 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="quiz-heading" className="text-2xl font-bold text-white mb-2">
                What music do you like?
              </h1>
              <p className="text-white/60">
                Select your favorite genres to get personalized recommendations.
              </p>
            </div>

            {/* Genre grid */}
            <div data-testid="genre-grid" className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
              {GENRES.map((genre) => (
                <button
                  key={genre.id}
                  data-testid={`genre-${genre.id}`}
                  onClick={() => toggleGenre(genre.id)}
                  className={`
                    p-4 rounded-xl text-left transition-all duration-200
                    ${
                      selectedGenres.has(genre.id)
                        ? "bg-gradient-to-r from-[#ff2d92]/20 to-[#b347ff]/20 border-[#ff2d92]/50 border-2 scale-[1.02]"
                        : "bg-white/5 border border-white/10 hover:border-white/30 hover:bg-white/10"
                    }
                  `}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="text-2xl">{genre.emoji}</span>
                      <div>
                        <h3 className="font-semibold text-white">{genre.label}</h3>
                        <p className="text-xs text-white/50 mt-0.5">
                          {genre.exampleArtists.join(", ")}
                        </p>
                      </div>
                    </div>
                    {selectedGenres.has(genre.id) && (
                      <div className="w-6 h-6 rounded-full bg-[#ff2d92] flex items-center justify-center flex-shrink-0">
                        <CheckIcon className="w-4 h-4 text-white" />
                      </div>
                    )}
                  </div>
                </button>
              ))}
            </div>

            {/* Selection count */}
            <div className="text-center mb-4">
              <span data-testid="genre-selection-count" className="text-white/40 text-sm">
                {selectedGenres.size === 0
                  ? "Select at least one genre"
                  : `${selectedGenres.size} genre${selectedGenres.size > 1 ? "s" : ""} selected`}
              </span>
            </div>

            {/* Navigation */}
            <div className="sticky bottom-4">
              <Button
                variant="primary"
                size="lg"
                className="w-full"
                onClick={() => setStep(2)}
                disabled={selectedGenres.size === 0}
                rightIcon={<ChevronRightIcon className="w-5 h-5" />}
              >
                Continue
              </Button>
            </div>
          </>
        )}

        {/* Step 2: Combined Preferences (Decade + Energy) */}
        {step === 2 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="preferences-heading" className="text-2xl font-bold text-white mb-2">
                Quick preferences
              </h1>
              <p className="text-white/60">
                Optional: Help us fine-tune your recommendations.
              </p>
            </div>

            {/* Decade preference */}
            <div data-testid="decade-section" className="mb-6">
              <h2 className="text-sm font-medium text-white/70 mb-3 uppercase tracking-wide">
                Favorite Era
              </h2>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
                {DECADES.map((d) => (
                  <button
                    key={d}
                    data-testid={`decade-${d}`}
                    onClick={() =>
                      setDecadePreference(decadePreference === d ? null : d)
                    }
                    className={`
                      py-3 px-2 rounded-xl text-center transition-all
                      ${
                        decadePreference === d
                          ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 border-2"
                          : "bg-white/5 border border-white/10 hover:border-white/20"
                      }
                    `}
                  >
                    <span className="text-sm font-medium text-white">{d}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Energy preference */}
            <div data-testid="energy-section" className="mb-8">
              <h2 className="text-sm font-medium text-white/70 mb-3 uppercase tracking-wide">
                Energy Level
              </h2>
              <div className="grid grid-cols-3 gap-3">
                {ENERGY_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    data-testid={`energy-${option.value}`}
                    onClick={() =>
                      setEnergyPreference(
                        energyPreference === option.value ? null : option.value
                      )
                    }
                    className={`
                      py-4 px-3 rounded-xl text-center transition-all
                      ${
                        energyPreference === option.value
                          ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 border-2"
                          : "bg-white/5 border border-white/10 hover:border-white/20"
                      }
                    `}
                  >
                    <span className="text-2xl block mb-1">{option.emoji}</span>
                    <span className="text-sm font-medium text-white">{option.label}</span>
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
                onClick={() => setStep(3)}
                rightIcon={<ChevronRightIcon className="w-5 h-5" />}
              >
                Continue
              </Button>
            </div>

            {/* Skip link */}
            <div className="text-center mt-4">
              <button
                onClick={handleSkipToRecommendations}
                disabled={isSubmitting}
                className="text-sm text-white/40 hover:text-white/60 transition-colors"
              >
                {isSubmitting ? "Loading..." : "Skip to recommendations →"}
              </button>
            </div>
          </>
        )}

        {/* Step 3: Artist Selection */}
        {step === 3 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="artist-heading" className="text-2xl font-bold text-white mb-2">
                Know any of these artists?
              </h1>
              <p className="text-white/60">
                Optional: Select artists you know for better recommendations.
              </p>
            </div>

            {isLoading ? (
              <LoadingPulse count={4} />
            ) : error ? (
              <div className="text-center py-8">
                <p className="text-white/60 mb-4">{error}</p>
                <Button onClick={() => loadQuizArtists()} variant="secondary">
                  Try again
                </Button>
              </div>
            ) : (
              <>
                {/* Selection count */}
                <div className="flex items-center justify-between mb-4">
                  <span data-testid="artist-selection-count" className="text-white/60 text-sm">
                    {selectedArtists.size} selected
                  </span>
                  {selectedArtists.size > 0 && (
                    <button
                      onClick={() => setSelectedArtists(new Set())}
                      className="text-sm text-white/40 hover:text-white transition-colors"
                    >
                      Clear all
                    </button>
                  )}
                </div>

                {/* Artist grid */}
                <div data-testid="artist-grid" className="grid grid-cols-1 gap-3 mb-4">
                  {quizArtists.map((artist, index) => (
                    <QuizArtistCard
                      key={artist.name}
                      artist={artist}
                      isSelected={selectedArtists.has(artist.name)}
                      onToggle={() => toggleArtist(artist.name)}
                      index={index}
                    />
                  ))}
                </div>

                {/* Load more artists button */}
                <div className="mb-6">
                  <Button
                    data-testid="load-more-artists-btn"
                    variant="ghost"
                    size="md"
                    className="w-full"
                    onClick={handleLoadMoreArtists}
                    isLoading={isLoadingMore}
                    leftIcon={<RefreshIcon className="w-4 h-4" />}
                  >
                    Show More Artists
                  </Button>
                </div>

                {/* Navigation */}
                <div className="flex gap-3 sticky bottom-4">
                  <Button
                    variant="secondary"
                    size="lg"
                    className="flex-1"
                    onClick={() => setStep(2)}
                  >
                    Back
                  </Button>
                  <Button
                    variant="primary"
                    size="lg"
                    className="flex-1"
                    onClick={handleSubmit}
                    isLoading={isSubmitting}
                    leftIcon={<SparklesIcon className="w-5 h-5" />}
                  >
                    See Recommendations
                  </Button>
                </div>

                {/* Skip link */}
                {selectedArtists.size === 0 && (
                  <div className="text-center mt-4">
                    <button
                      onClick={handleSubmit}
                      disabled={isSubmitting}
                      className="text-sm text-white/40 hover:text-white/60 transition-colors"
                    >
                      {isSubmitting ? "Loading..." : "Skip, I don't know any →"}
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </main>
  );
}
