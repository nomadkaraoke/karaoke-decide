"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { QuizArtistCard } from "@/components/QuizArtistCard";
import { SongSearchAutocomplete, SelectedSong } from "@/components/SongSearchAutocomplete";
import { EnjoySingingModal, EnjoySingingMetadataResult } from "@/components/EnjoySingingModal";
import { SparklesIcon, CheckIcon, ChevronRightIcon, RefreshIcon, MicrophoneIcon, TrashIcon, XIcon } from "@/components/icons";
import { Button, LoadingPulse, LoadingOverlay } from "@/components/ui";
import type { SingingTag, SingingEnergy, VocalComfort } from "@/types";

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

// 4-step quiz:
// Step 1: Genres (required)
// Step 2: Preferences - combined decade + energy (optional)
// Step 3: Artists (optional)
// Step 4: Songs I Enjoy Singing (optional)
type QuizStep = 1 | 2 | 3 | 4;

// Type for songs user enjoys singing in the quiz
interface EnjoySongSelection extends SelectedSong {
  singing_tags?: SingingTag[];
  singing_energy?: SingingEnergy | null;
  vocal_comfort?: VocalComfort | null;
  notes?: string | null;
}

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

  // Step 4: Songs I enjoy singing
  const [enjoySongs, setEnjoySongs] = useState<EnjoySongSelection[]>([]);
  const [showEnjoySingingModal, setShowEnjoySingingModal] = useState(false);
  const [selectedSongForModal, setSelectedSongForModal] = useState<EnjoySongSelection | null>(null);

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

      // Submit main quiz data
      await api.quiz.submit({
        known_artists: Array.from(selectedArtists),
        decade_preference: decadePreference,
        energy_preference: energyPreference,
      });

      // Submit enjoy singing songs if any
      if (enjoySongs.length > 0) {
        await api.quiz.submitEnjoySinging(
          enjoySongs.map((song) => ({
            song_id: song.song_id,
            singing_tags: song.singing_tags,
            singing_energy: song.singing_energy,
            vocal_comfort: song.vocal_comfort,
            notes: song.notes,
          }))
        );
      }

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

  // Step 4: Enjoy singing handlers
  const handleAddEnjoySong = (song: SelectedSong) => {
    const newSong: EnjoySongSelection = {
      ...song,
      singing_tags: [],
      singing_energy: null,
      vocal_comfort: null,
      notes: null,
    };
    setEnjoySongs((prev) => [...prev, newSong]);
  };

  const handleRemoveEnjoySong = (songId: string) => {
    setEnjoySongs((prev) => prev.filter((s) => s.song_id !== songId));
  };

  const handleEditEnjoySong = (song: EnjoySongSelection) => {
    setSelectedSongForModal(song);
    setShowEnjoySingingModal(true);
  };

  const handleLocalSaveMetadata = (metadata: EnjoySingingMetadataResult) => {
    if (!selectedSongForModal) return;

    // Update the song in local state with the new metadata
    setEnjoySongs((prev) =>
      prev.map((song) =>
        song.song_id === selectedSongForModal.song_id
          ? {
              ...song,
              singing_tags: metadata.singing_tags,
              singing_energy: metadata.singing_energy,
              vocal_comfort: metadata.vocal_comfort,
              notes: metadata.notes,
            }
          : song
      )
    );
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
          {[1, 2, 3, 4].map((s) => (
            <div
              key={s}
              data-testid={`progress-dot-${s}`}
              className={`w-3 h-3 rounded-full transition-colors ${
                s === step
                  ? "bg-[var(--brand-pink)]"
                  : s < step
                    ? "bg-[var(--brand-pink)]/50"
                    : "bg-[var(--secondary)]"
              }`}
            />
          ))}
        </div>

        {/* Step 1: Genre Selection */}
        {step === 1 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="quiz-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                What music do you like?
              </h1>
              <p className="text-[var(--text)]/60">
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
                        ? "bg-gradient-to-r from-[var(--brand-pink)]/20 to-[var(--brand-purple)]/20 border-[var(--brand-pink)]/50 border-2 scale-[1.02]"
                        : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--primary)] hover:bg-[var(--secondary)]"
                    }
                  `}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="text-2xl">{genre.emoji}</span>
                      <div>
                        <h3 className="font-semibold text-[var(--text)]">{genre.label}</h3>
                        <p className="text-xs text-[var(--text)]/50 mt-0.5">
                          {genre.exampleArtists.join(", ")}
                        </p>
                      </div>
                    </div>
                    {selectedGenres.has(genre.id) && (
                      <div className="w-6 h-6 rounded-full bg-[var(--brand-pink)] flex items-center justify-center flex-shrink-0">
                        <CheckIcon className="w-4 h-4 text-[var(--text)]" />
                      </div>
                    )}
                  </div>
                </button>
              ))}
            </div>

            {/* Selection count */}
            <div className="text-center mb-4">
              <span data-testid="genre-selection-count" className="text-[var(--text)]/40 text-sm">
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
              <h1 data-testid="preferences-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Quick preferences
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Help us fine-tune your recommendations.
              </p>
            </div>

            {/* Decade preference */}
            <div data-testid="decade-section" className="mb-6">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-3 uppercase tracking-wide">
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
                          ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                          : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--primary)]/50"
                      }
                    `}
                  >
                    <span className="text-sm font-medium text-[var(--text)]">{d}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Energy preference */}
            <div data-testid="energy-section" className="mb-8">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-3 uppercase tracking-wide">
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
                          ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                          : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--primary)]/50"
                      }
                    `}
                  >
                    <span className="text-2xl block mb-1">{option.emoji}</span>
                    <span className="text-sm font-medium text-[var(--text)]">{option.label}</span>
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
                className="text-sm text-[var(--text)]/40 hover:text-[var(--text)]/60 transition-colors"
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
              <h1 data-testid="artist-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Know any of these artists?
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Select artists you know for better recommendations.
              </p>
            </div>

            {isLoading ? (
              <LoadingPulse count={4} />
            ) : error ? (
              <div className="text-center py-8">
                <p className="text-[var(--text)]/60 mb-4">{error}</p>
                <Button onClick={() => loadQuizArtists()} variant="secondary">
                  Try again
                </Button>
              </div>
            ) : (
              <>
                {/* Selection count */}
                <div className="flex items-center justify-between mb-4">
                  <span data-testid="artist-selection-count" className="text-[var(--text)]/60 text-sm">
                    {selectedArtists.size} selected
                  </span>
                  {selectedArtists.size > 0 && (
                    <button
                      onClick={() => setSelectedArtists(new Set())}
                      className="text-sm text-[var(--text)]/40 hover:text-[var(--text)] transition-colors"
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
                    onClick={() => setStep(4)}
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
                    className="text-sm text-[var(--text)]/40 hover:text-[var(--text)]/60 transition-colors"
                  >
                    {isSubmitting ? "Loading..." : "Skip to recommendations →"}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        {/* Step 4: Songs I Enjoy Singing */}
        {step === 4 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="enjoy-singing-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Songs you love to sing
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Add songs you already know you enjoy singing at karaoke.
              </p>
            </div>

            {/* Song search */}
            <div className="mb-6">
              <SongSearchAutocomplete
                onSelect={handleAddEnjoySong}
                selectedSongIds={new Set(enjoySongs.map((s) => s.song_id))}
                placeholder="Search for songs you love singing..."
              />
            </div>

            {/* Selected songs */}
            {enjoySongs.length > 0 && (
              <div className="mb-6">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-medium text-[var(--text)]/70 uppercase tracking-wide">
                    Songs Added ({enjoySongs.length})
                  </h2>
                  {enjoySongs.length > 0 && (
                    <button
                      onClick={() => setEnjoySongs([])}
                      className="text-sm text-[var(--text)]/40 hover:text-[var(--text)] transition-colors"
                    >
                      Clear all
                    </button>
                  )}
                </div>
                <div className="space-y-2">
                  {enjoySongs.map((song) => (
                    <div
                      key={song.song_id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)]"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-[var(--text)] font-medium truncate">
                            {song.title}
                          </span>
                          {(song.singing_tags && song.singing_tags.length > 0) && (
                            <span className="shrink-0 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-[var(--brand-pink)]/20 text-[var(--brand-pink)]">
                              Tagged
                            </span>
                          )}
                        </div>
                        <span className="text-[var(--text-muted)] text-sm truncate block">
                          {song.artist}
                        </span>
                      </div>
                      {/* Edit metadata button */}
                      <button
                        onClick={() => handleEditEnjoySong(song)}
                        className="p-2 rounded-full text-[var(--text-subtle)] hover:text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/10 transition-colors"
                        title="Add details about why you love this song"
                      >
                        <MicrophoneIcon className="w-4 h-4" />
                      </button>
                      {/* Remove button */}
                      <button
                        onClick={() => handleRemoveEnjoySong(song.song_id)}
                        className="p-2 rounded-full text-[var(--text-subtle)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                        title="Remove song"
                      >
                        <XIcon className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Empty state hint */}
            {enjoySongs.length === 0 && (
              <div className="text-center py-8 mb-6">
                <MicrophoneIcon className="w-12 h-12 text-[var(--text-subtle)] mx-auto mb-3" />
                <p className="text-[var(--text)]/40 text-sm">
                  Search above to add songs you already love singing.
                  <br />
                  This helps us find similar songs for you!
                </p>
              </div>
            )}

            {/* Navigation */}
            <div className="flex gap-3 sticky bottom-4">
              <Button
                variant="secondary"
                size="lg"
                className="flex-1"
                onClick={() => setStep(3)}
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
            {enjoySongs.length === 0 && (
              <div className="text-center mt-4">
                <button
                  onClick={handleSubmit}
                  disabled={isSubmitting}
                  className="text-sm text-[var(--text)]/40 hover:text-[var(--text)]/60 transition-colors"
                >
                  {isSubmitting ? "Loading..." : "Skip, I'll add songs later →"}
                </button>
              </div>
            )}

            {/* Enjoy Singing Modal - uses local save mode for quiz */}
            {selectedSongForModal && (
              <EnjoySingingModal
                isOpen={showEnjoySingingModal}
                onClose={() => {
                  setShowEnjoySingingModal(false);
                  setSelectedSongForModal(null);
                }}
                onLocalSave={handleLocalSaveMetadata}
                song={{
                  song_id: selectedSongForModal.song_id,
                  artist: selectedSongForModal.artist,
                  title: selectedSongForModal.title,
                  enjoy_singing: true,
                  singing_tags: selectedSongForModal.singing_tags,
                  singing_energy: selectedSongForModal.singing_energy,
                  vocal_comfort: selectedSongForModal.vocal_comfort,
                  notes: selectedSongForModal.notes,
                }}
              />
            )}
          </>
        )}
      </div>
    </main>
  );
}
