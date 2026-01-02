"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { QuizArtistCard } from "@/components/QuizArtistCard";
import { SparklesIcon, CheckIcon, ChevronRightIcon, RefreshIcon, SpotifyIcon } from "@/components/icons";
import { Button, LoadingPulse, LoadingOverlay } from "@/components/ui";

interface QuizArtist {
  name: string;
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;
  image_url: string | null;
}

type EnergyPreference = "chill" | "medium" | "high" | null;

const DECADES = ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s"];
const ENERGY_OPTIONS: { value: EnergyPreference; label: string; description: string }[] = [
  { value: "chill", label: "Chill", description: "Slow ballads, easy listening" },
  { value: "medium", label: "Medium", description: "Classic sing-alongs" },
  { value: "high", label: "High Energy", description: "Dance hits, rock anthems" },
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
  { id: "country", label: "Country", exampleArtists: ["Luke Combs", "Carrie Underwood", "Dolly Parton"], emoji: "🤠" },
  { id: "hiphop", label: "Hip-Hop", exampleArtists: ["Drake", "Eminem", "Kendrick Lamar"], emoji: "🎧" },
  { id: "rnb", label: "R&B / Soul", exampleArtists: ["Beyoncé", "Usher", "Alicia Keys"], emoji: "🎹" },
  { id: "classic-rock", label: "Classic Rock", exampleArtists: ["Journey", "Eagles", "Fleetwood Mac"], emoji: "🎷" },
  { id: "80s-90s", label: "80s & 90s Hits", exampleArtists: ["Michael Jackson", "Whitney Houston", "Prince"], emoji: "💿" },
];

export default function QuizPage() {
  const router = useRouter();
  const { isAuthenticated, isLoading: authLoading, startGuestSession, isGuest } = useAuth();
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [quizArtists, setQuizArtists] = useState<QuizArtist[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // User selections
  const [selectedGenres, setSelectedGenres] = useState<Set<string>>(new Set());
  const [selectedArtists, setSelectedArtists] = useState<Set<string>>(new Set());
  const [decadePreference, setDecadePreference] = useState<string | null>(null);
  const [energyPreference, setEnergyPreference] = useState<EnergyPreference>(null);
  const [skipArtists, setSkipArtists] = useState(false);

  // Submit state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showConnectCTA, setShowConnectCTA] = useState(true);
  const [submitResult, setSubmitResult] = useState<{
    songs_added: number;
    recommendations_ready: boolean;
  } | null>(null);

  const loadQuizArtists = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.quiz.getArtists(25);
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

  // Load quiz artists once authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadQuizArtists();
    }
  }, [isAuthenticated, loadQuizArtists]);

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
    // If user selects something, uncheck the skip option
    if (!selectedArtists.has(artistName)) {
      setSkipArtists(false);
    }
  };

  const handleSkipToggle = () => {
    setSkipArtists(!skipArtists);
    if (!skipArtists) {
      // If enabling skip, clear selections
      setSelectedArtists(new Set());
    }
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

  const handleRefreshArtists = async () => {
    try {
      setIsRefreshing(true);
      const response = await api.quiz.getArtists(25);
      // Preserve selections that appear in new batch
      const newArtistNames = new Set(response.artists.map((a) => a.name));
      setSelectedArtists((prev) => {
        const preserved = new Set<string>();
        prev.forEach((name) => {
          if (newArtistNames.has(name)) {
            preserved.add(name);
          }
        });
        return preserved;
      });
      setQuizArtists(response.artists);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load artists");
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleConnectSpotify = async () => {
    if (isGuest) {
      // Redirect to services page which will show upgrade prompt for guests
      router.push("/services");
      return;
    }
    try {
      const response = await api.services.connectSpotify();
      window.location.href = response.auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect Spotify");
    }
  };

  const handleSubmit = async () => {
    try {
      setIsSubmitting(true);
      const response = await api.quiz.submit({
        known_artists: Array.from(selectedArtists),
        decade_preference: decadePreference,
        energy_preference: energyPreference,
      });
      setSubmitResult(response);
      setStep(4);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit quiz");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Show loading while auth is being checked
  if (authLoading || !isAuthenticated) {
    return <LoadingOverlay message="Starting quiz..." />;
  }

  return (
    <main className="min-h-screen pb-safe">
      <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Progress indicator */}
          <div data-testid="progress-indicator" className="flex items-center justify-center gap-2 mb-8">
            {[1, 2, 3, 4].map((s) => (
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
                  Select your favorite genres to help us find karaoke songs you&apos;ll love.
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
                    ? "Select genres or skip to continue"
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
                  rightIcon={<ChevronRightIcon className="w-5 h-5" />}
                >
                  {selectedGenres.size === 0 ? "Skip" : "Continue"}
                </Button>
              </div>
            </>
          )}

          {/* Step 2: Artist Selection */}
          {step === 2 && (
            <>
              <div className="text-center mb-8">
                <h1 data-testid="artist-heading" className="text-2xl font-bold text-white mb-2">
                  Which artists do you know?
                </h1>
                <p className="text-white/60">
                  Select artists you listen to or recognize. This helps us find
                  karaoke songs you&apos;ll love.
                </p>
              </div>

              {isLoading ? (
                <LoadingPulse count={6} />
              ) : error ? (
                <div className="text-center py-8">
                  <p className="text-white/60 mb-4">{error}</p>
                  <Button onClick={loadQuizArtists} variant="secondary">
                    Try again
                  </Button>
                </div>
              ) : (
                <>
                  {/* Selection count and refresh */}
                  <div className="flex items-center justify-between mb-4">
                    <span data-testid="artist-selection-count" className="text-white/60 text-sm">
                      {selectedArtists.size} selected
                    </span>
                    <div className="flex items-center gap-3">
                      <Button
                        data-testid="refresh-artists-btn"
                        variant="ghost"
                        size="sm"
                        onClick={handleRefreshArtists}
                        isLoading={isRefreshing}
                        leftIcon={<RefreshIcon className="w-4 h-4" />}
                      >
                        Show Different Artists
                      </Button>
                      {selectedArtists.size > 0 && (
                        <button
                          onClick={() => setSelectedArtists(new Set())}
                          className="text-sm text-white/40 hover:text-white transition-colors"
                        >
                          Clear all
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Artist grid */}
                  <div data-testid="artist-grid" className="grid grid-cols-1 gap-3 mb-6">
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

                  {/* Skip option */}
                  <div className="mb-8">
                    <button
                      data-testid="skip-artists-btn"
                      onClick={handleSkipToggle}
                      className={`
                        w-full p-4 rounded-xl text-left transition-all
                        ${
                          skipArtists
                            ? "bg-white/10 border-white/30 border"
                            : "bg-white/5 border border-white/10 hover:border-white/20"
                        }
                      `}
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className={`
                            w-5 h-5 rounded-full border-2 flex items-center justify-center
                            ${skipArtists ? "border-[#00f5ff] bg-[#00f5ff]/20" : "border-white/30"}
                          `}
                        >
                          {skipArtists && <CheckIcon className="w-3 h-3 text-[#00f5ff]" />}
                        </div>
                        <div>
                          <p className="text-white font-medium">I don&apos;t know any of these</p>
                          <p className="text-white/50 text-sm">
                            We&apos;ll show you popular crowd-pleasers instead
                          </p>
                        </div>
                      </div>
                    </button>
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
                </>
              )}
            </>
          )}

          {/* Step 3: Preferences */}
          {step === 3 && (
            <>
              <div className="text-center mb-8">
                <h1 data-testid="preferences-heading" className="text-2xl font-bold text-white mb-2">
                  Your preferences
                </h1>
                <p className="text-white/60">
                  Optional: Help us fine-tune your recommendations.
                </p>
              </div>

              {/* Decade preference */}
              <div data-testid="decade-section" className="mb-8">
                <h2 className="text-lg font-semibold text-white mb-3">
                  Favorite decade?
                </h2>
                <div className="flex flex-wrap gap-2">
                  {DECADES.map((d) => (
                    <button
                      key={d}
                      data-testid={`decade-${d}`}
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
              <div data-testid="energy-section" className="mb-8">
                <h2 className="text-lg font-semibold text-white mb-3">
                  What energy level?
                </h2>
                <div className="grid grid-cols-1 gap-3">
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
                >
                  Finish Quiz
                </Button>
              </div>
            </>
          )}

          {/* Step 4: Results */}
          {step === 4 && submitResult && (
            <div data-testid="results-section" className="py-8">
              {/* Success indicator */}
              <div className="text-center">
                <div className="relative w-20 h-20 mx-auto mb-6">
                  <div className="absolute inset-0 bg-green-500/20 rounded-full animate-pulse" />
                  <div className="relative w-full h-full rounded-full bg-green-500/10 flex items-center justify-center border border-green-500/30">
                    <CheckIcon className="w-10 h-10 text-green-400" />
                  </div>
                </div>

                <h1 data-testid="results-heading" className="text-2xl font-bold text-white mb-2">
                  Quiz Complete!
                </h1>
                <p data-testid="results-message" className="text-white/60 mb-8">
                  {submitResult.songs_added > 0
                    ? `We found ${submitResult.songs_added} karaoke songs based on your selections.`
                    : "We'll show you popular crowd-pleasers to get you started."}
                  {submitResult.recommendations_ready &&
                    " Your personalized recommendations are ready!"}
                </p>

                {/* Primary actions */}
                <div className="space-y-3 mb-8">
                  <Button
                    data-testid="view-recommendations-btn"
                    variant="primary"
                    size="lg"
                    className="w-full"
                    onClick={() => router.push("/recommendations")}
                    leftIcon={<SparklesIcon className="w-5 h-5" />}
                  >
                    View Recommendations
                  </Button>
                  <Button
                    data-testid="view-my-songs-btn"
                    variant="secondary"
                    size="lg"
                    className="w-full"
                    onClick={() => router.push("/my-songs")}
                  >
                    View My Songs
                  </Button>
                </div>
              </div>

              {/* Connect Services CTA */}
              {showConnectCTA && (
                <div data-testid="connect-cta" className="p-6 rounded-2xl bg-gradient-to-br from-[#ff2d92]/10 via-[#b347ff]/5 to-[#00f5ff]/10 border border-white/10">
                  <div className="text-center mb-4">
                    <h2 data-testid="connect-cta-heading" className="text-lg font-semibold text-white mb-2">
                      Want even better recommendations?
                    </h2>
                    <p className="text-white/60 text-sm">
                      {isGuest
                        ? "Create an account and connect your music services for personalized suggestions based on your actual listening history."
                        : "Connect your music services to get personalized suggestions based on your actual listening history."}
                    </p>
                  </div>

                  <div className="space-y-3">
                    <Button
                      data-testid="connect-spotify-btn"
                      variant="secondary"
                      size="md"
                      className="w-full justify-center"
                      onClick={handleConnectSpotify}
                      leftIcon={<SpotifyIcon className="w-5 h-5 text-[#1DB954]" />}
                    >
                      {isGuest ? "Create Account & Connect Spotify" : "Connect Spotify"}
                    </Button>

                    {/* Apple Music - placeholder for future */}
                    <Button
                      data-testid="connect-apple-music-btn"
                      variant="secondary"
                      size="md"
                      className="w-full justify-center opacity-50"
                      disabled
                      leftIcon={<span className="text-lg">🎵</span>}
                    >
                      Apple Music (Coming Soon)
                    </Button>
                  </div>

                  <button
                    data-testid="connect-cta-dismiss"
                    onClick={() => setShowConnectCTA(false)}
                    className="w-full mt-4 text-sm text-white/40 hover:text-white/60 transition-colors"
                  >
                    Maybe later
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
  );
}
