"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { QuizArtistCard } from "@/components/QuizArtistCard";
import { SongSearchAutocomplete, SelectedSong } from "@/components/SongSearchAutocomplete";
import { ArtistSearchAutocomplete, SelectedArtist } from "@/components/ArtistSearchAutocomplete";
import { EnjoySingingModal, EnjoySingingMetadataResult } from "@/components/EnjoySingingModal";
import { SparklesIcon, CheckIcon, ChevronRightIcon, RefreshIcon, MicrophoneIcon, XIcon } from "@/components/icons";
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
type VocalComfortPref = "easy" | "challenging" | "any" | null;
type CrowdPleaserPref = "hits" | "deep_cuts" | "any" | null;

// Expanded decades including 1950s and 1960s
const DECADES = ["1950s", "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"];

const ENERGY_OPTIONS: { value: EnergyPreference; label: string; emoji: string }[] = [
  { value: "chill", label: "Chill", emoji: "🎵" },
  { value: "medium", label: "Medium", emoji: "🎶" },
  { value: "high", label: "High Energy", emoji: "🔥" },
];

const VOCAL_COMFORT_OPTIONS: { value: VocalComfortPref; label: string; description: string; emoji: string }[] = [
  { value: "easy", label: "Easy Songs", description: "Comfortable vocal range", emoji: "😌" },
  { value: "challenging", label: "Show Off", description: "Love vocal challenges", emoji: "💪" },
  { value: "any", label: "Any", description: "No preference", emoji: "🎤" },
];

const CROWD_PLEASER_OPTIONS: { value: CrowdPleaserPref; label: string; description: string; emoji: string }[] = [
  { value: "hits", label: "Popular Hits", description: "Songs everyone knows", emoji: "🌟" },
  { value: "deep_cuts", label: "Deep Cuts", description: "Hidden gems & B-sides", emoji: "💎" },
  { value: "any", label: "Mix", description: "Give me both", emoji: "🎲" },
];

interface Genre {
  id: string;
  label: string;
  exampleArtists: string[];
  emoji: string;
}

// Expanded genres with punk, emo, grunge, folk, blues, ska, and "other"
const GENRES: Genre[] = [
  { id: "pop", label: "Pop", exampleArtists: ["Taylor Swift", "Ed Sheeran", "Dua Lipa"], emoji: "🎤" },
  { id: "rock", label: "Rock", exampleArtists: ["Queen", "Bon Jovi", "Foo Fighters"], emoji: "🎸" },
  { id: "hiphop", label: "Hip-Hop / Rap", exampleArtists: ["Drake", "Eminem", "Kendrick Lamar"], emoji: "🎧" },
  { id: "rnb", label: "R&B / Soul", exampleArtists: ["Beyoncé", "Usher", "Alicia Keys"], emoji: "🎹" },
  { id: "country", label: "Country", exampleArtists: ["Luke Combs", "Carrie Underwood", "Dolly Parton"], emoji: "🤠" },
  { id: "electronic", label: "Electronic / Dance", exampleArtists: ["Daft Punk", "Calvin Harris", "The Chainsmokers"], emoji: "🎛️" },
  { id: "metal", label: "Metal / Hard Rock", exampleArtists: ["Metallica", "AC/DC", "Iron Maiden"], emoji: "🤘" },
  { id: "punk", label: "Punk", exampleArtists: ["Green Day", "Blink-182", "The Offspring"], emoji: "⚡" },
  { id: "emo", label: "Emo / Goth", exampleArtists: ["My Chemical Romance", "Fall Out Boy", "Paramore"], emoji: "🖤" },
  { id: "grunge", label: "Grunge", exampleArtists: ["Nirvana", "Pearl Jam", "Soundgarden"], emoji: "🔊" },
  { id: "jazz", label: "Jazz / Standards", exampleArtists: ["Frank Sinatra", "Ella Fitzgerald", "Michael Bublé"], emoji: "🎺" },
  { id: "latin", label: "Latin", exampleArtists: ["Bad Bunny", "Shakira", "J Balvin"], emoji: "💃" },
  { id: "indie", label: "Indie / Alternative", exampleArtists: ["Arctic Monkeys", "The 1975", "Tame Impala"], emoji: "🌙" },
  { id: "kpop", label: "K-Pop", exampleArtists: ["BTS", "BLACKPINK", "Stray Kids"], emoji: "🇰🇷" },
  { id: "disco", label: "Disco / Funk", exampleArtists: ["ABBA", "Earth, Wind & Fire", "Bee Gees"], emoji: "🕺" },
  { id: "classic-rock", label: "Classic Rock", exampleArtists: ["Journey", "Eagles", "Fleetwood Mac"], emoji: "🎷" },
  { id: "folk", label: "Folk / Acoustic", exampleArtists: ["Mumford & Sons", "The Lumineers", "Hozier"], emoji: "🪕" },
  { id: "blues", label: "Blues", exampleArtists: ["B.B. King", "Eric Clapton", "John Mayer"], emoji: "🎸" },
  { id: "ska", label: "Ska", exampleArtists: ["No Doubt", "Sublime", "Reel Big Fish"], emoji: "🎺" },
  { id: "musical", label: "Broadway / Musical", exampleArtists: ["Wicked", "Les Misérables", "Hamilton"], emoji: "🎭" },
  { id: "reggae", label: "Reggae", exampleArtists: ["Bob Marley", "UB40", "Sean Paul"], emoji: "🇯🇲" },
  { id: "other", label: "Other / Not Sure", exampleArtists: ["I'll browse recommendations"], emoji: "❓" },
];

// 5-step quiz:
// Step 1: Genres (required)
// Step 2: Favorite Eras - multi-select decades (optional)
// Step 3: Quick Preferences - energy + vocal comfort + crowd pleaser (optional)
// Step 4: Music You Know - manual artist/song entry (optional)
// Step 5: Artists You Know - smart artist selection (optional)
type QuizStep = 1 | 2 | 3 | 4 | 5;

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
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // User selections
  const [selectedGenres, setSelectedGenres] = useState<Set<string>>(new Set());
  const [selectedDecades, setSelectedDecades] = useState<Set<string>>(new Set());
  const [selectedArtists, setSelectedArtists] = useState<Set<string>>(new Set());
  const [energyPreference, setEnergyPreference] = useState<EnergyPreference>(null);
  const [vocalComfortPref, setVocalComfortPref] = useState<VocalComfortPref>(null);
  const [crowdPleaserPref, setCrowdPleaserPref] = useState<CrowdPleaserPref>(null);

  // Step 4: Manual artist/song entry
  const [manualArtists, setManualArtists] = useState<SelectedArtist[]>([]);
  const [enjoySongs, setEnjoySongs] = useState<EnjoySongSelection[]>([]);
  const [showEnjoySingingModal, setShowEnjoySingingModal] = useState(false);
  const [selectedSongForModal, setSelectedSongForModal] = useState<EnjoySongSelection | null>(null);

  // Submit state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // Load smart artists for step 5 (informed by previous selections)
  const loadSmartArtists = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      // Get artists from songs user enjoys singing
      const songArtists = enjoySongs.map((s) => s.artist);

      const response = await api.quiz.getSmartArtists({
        genres: selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined,
        decades: selectedDecades.size > 0 ? Array.from(selectedDecades) : undefined,
        manual_artists: manualArtists.length > 0 ? manualArtists.map(a => a.artist_name) : undefined,
        manual_song_artists: songArtists.length > 0 ? songArtists : undefined,
        exclude: Array.from(selectedArtists),
        count: 15,
      });

      setQuizArtists(response.artists);
    } catch (err) {
      // Fallback to regular artist loading if smart endpoint fails
      console.error("Smart artist loading failed, falling back:", err);
      try {
        const genresArray = selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined;
        const response = await api.quiz.getArtists(15, genresArray);
        setQuizArtists(response.artists);
      } catch (fallbackErr) {
        setError(fallbackErr instanceof Error ? fallbackErr.message : "Failed to load artists");
      }
    } finally {
      setIsLoading(false);
    }
  }, [selectedGenres, selectedDecades, manualArtists, enjoySongs, selectedArtists]);

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

  // Load smart artists when entering step 5
  useEffect(() => {
    if (isAuthenticated && step === 5) {
      loadSmartArtists();
    }
  }, [isAuthenticated, step, loadSmartArtists]);

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

  const toggleDecade = (decade: string) => {
    setSelectedDecades((prev) => {
      const next = new Set(prev);
      if (next.has(decade)) {
        next.delete(decade);
      } else {
        next.add(decade);
      }
      return next;
    });
  };

  const handleLoadMoreArtists = async () => {
    try {
      setIsLoadingMore(true);
      const currentArtistNames = quizArtists.map((a) => a.name);
      const songArtists = enjoySongs.map((s) => s.artist);

      const response = await api.quiz.getSmartArtists({
        genres: selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined,
        decades: selectedDecades.size > 0 ? Array.from(selectedDecades) : undefined,
        manual_artists: manualArtists.length > 0 ? manualArtists.map(a => a.artist_name) : undefined,
        manual_song_artists: songArtists.length > 0 ? songArtists : undefined,
        exclude: [...Array.from(selectedArtists), ...currentArtistNames],
        count: 10,
      });

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

      // Submit main quiz data with all new fields
      await api.quiz.submit({
        known_artists: Array.from(selectedArtists),
        decade_preferences: Array.from(selectedDecades),
        energy_preference: energyPreference,
        genres: Array.from(selectedGenres),
        vocal_comfort_pref: vocalComfortPref,
        crowd_pleaser_pref: crowdPleaserPref,
        manual_artists: manualArtists,
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
    await handleSubmit();
  };

  // Step 4: Manual artist handlers
  const handleAddManualArtist = (artist: SelectedArtist) => {
    // Don't add duplicates
    if (!manualArtists.some((a) => a.artist_id === artist.artist_id)) {
      setManualArtists((prev) => [...prev, artist]);
    }
  };

  const handleRemoveManualArtist = (artistId: string) => {
    setManualArtists((prev) => prev.filter((a) => a.artist_id !== artistId));
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
          {[1, 2, 3, 4, 5].map((s) => (
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

        {/* Step 2: Favorite Eras (Multi-select Decades) */}
        {step === 2 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="decades-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Favorite eras
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Select the decades of music you enjoy most.
              </p>
            </div>

            {/* Decade grid - multi-select */}
            <div data-testid="decade-section" className="mb-8">
              <div className="grid grid-cols-4 gap-2">
                {DECADES.map((d) => (
                  <button
                    key={d}
                    data-testid={`decade-${d}`}
                    onClick={() => toggleDecade(d)}
                    className={`
                      py-4 px-2 rounded-xl text-center transition-all
                      ${
                        selectedDecades.has(d)
                          ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                          : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--primary)]/50"
                      }
                    `}
                  >
                    <span className="text-sm font-medium text-[var(--text)]">{d}</span>
                    {selectedDecades.has(d) && (
                      <CheckIcon className="w-3 h-3 text-[var(--brand-pink)] mx-auto mt-1" />
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Selection count */}
            <div className="text-center mb-4">
              <span className="text-[var(--text)]/40 text-sm">
                {selectedDecades.size === 0
                  ? "No era preference (all decades)"
                  : `${selectedDecades.size} era${selectedDecades.size > 1 ? "s" : ""} selected`}
              </span>
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

        {/* Step 3: Quick Preferences (Energy + Vocal Comfort + Crowd Pleaser) */}
        {step === 3 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="preferences-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Quick preferences
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Help us fine-tune your recommendations.
              </p>
            </div>

            {/* Energy preference */}
            <div data-testid="energy-section" className="mb-6">
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

            {/* Vocal Comfort preference */}
            <div data-testid="vocal-comfort-section" className="mb-6">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-3 uppercase tracking-wide">
                Vocal Comfort
              </h2>
              <div className="grid grid-cols-3 gap-3">
                {VOCAL_COMFORT_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    data-testid={`vocal-comfort-${option.value}`}
                    onClick={() =>
                      setVocalComfortPref(
                        vocalComfortPref === option.value ? null : option.value
                      )
                    }
                    className={`
                      py-4 px-3 rounded-xl text-center transition-all
                      ${
                        vocalComfortPref === option.value
                          ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                          : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--primary)]/50"
                      }
                    `}
                  >
                    <span className="text-2xl block mb-1">{option.emoji}</span>
                    <span className="text-sm font-medium text-[var(--text)]">{option.label}</span>
                    <span className="text-xs text-[var(--text)]/50 block mt-0.5">{option.description}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Crowd Pleaser preference */}
            <div data-testid="crowd-pleaser-section" className="mb-8">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-3 uppercase tracking-wide">
                Song Discovery
              </h2>
              <div className="grid grid-cols-3 gap-3">
                {CROWD_PLEASER_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    data-testid={`crowd-pleaser-${option.value}`}
                    onClick={() =>
                      setCrowdPleaserPref(
                        crowdPleaserPref === option.value ? null : option.value
                      )
                    }
                    className={`
                      py-4 px-3 rounded-xl text-center transition-all
                      ${
                        crowdPleaserPref === option.value
                          ? "bg-[var(--brand-pink)]/20 border-[var(--brand-pink)]/50 border-2"
                          : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--primary)]/50"
                      }
                    `}
                  >
                    <span className="text-2xl block mb-1">{option.emoji}</span>
                    <span className="text-sm font-medium text-[var(--text)]">{option.label}</span>
                    <span className="text-xs text-[var(--text)]/50 block mt-0.5">{option.description}</span>
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

        {/* Step 4: Music You Know (Manual Artist/Song Entry) */}
        {step === 4 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="music-you-know-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Music you know
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Tell us about artists and songs you already love.
              </p>
            </div>

            {/* Manual Artist Entry */}
            <div className="mb-6">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-3 uppercase tracking-wide">
                Artists you like
              </h2>
              <ArtistSearchAutocomplete
                onSelect={handleAddManualArtist}
                selectedArtistIds={new Set(manualArtists.map((a) => a.artist_id))}
                placeholder="Search for artists you like..."
                className="mb-3"
              />
              {manualArtists.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {manualArtists.map((artist) => (
                    <span
                      key={artist.artist_id}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full bg-[var(--brand-pink)]/20 text-[var(--brand-pink)] text-sm font-medium"
                    >
                      {artist.artist_name}
                      <button
                        onClick={() => handleRemoveManualArtist(artist.artist_id)}
                        className="hover:text-[var(--text)] transition-colors"
                      >
                        <XIcon className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Songs I Enjoy Singing */}
            <div className="mb-6">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-3 uppercase tracking-wide">
                Songs you love to sing
              </h2>
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
                  <h3 className="text-sm font-medium text-[var(--text)]/70">
                    Songs Added ({enjoySongs.length})
                  </h3>
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
                      <button
                        onClick={() => handleEditEnjoySong(song)}
                        className="p-2 rounded-full text-[var(--text-subtle)] hover:text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/10 transition-colors"
                        title="Add details about why you love this song"
                      >
                        <MicrophoneIcon className="w-4 h-4" />
                      </button>
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

            {/* Hint when empty */}
            {manualArtists.length === 0 && enjoySongs.length === 0 && (
              <div className="text-center py-6 mb-6">
                <p className="text-[var(--text)]/40 text-sm">
                  Adding artists and songs helps us find better recommendations.
                  <br />
                  You can skip this step if you prefer.
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
                onClick={() => setStep(5)}
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

            {/* Enjoy Singing Modal */}
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

        {/* Step 5: Artists You Know (Smart Selection - informed by previous steps) */}
        {step === 5 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="artist-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Know any of these artists?
              </h1>
              <p className="text-[var(--text)]/60">
                Optional: Based on your preferences, you might know these artists.
              </p>
            </div>

            {isLoading ? (
              <LoadingPulse count={4} />
            ) : error ? (
              <div className="text-center py-8">
                <p className="text-[var(--text)]/60 mb-4">{error}</p>
                <Button onClick={() => loadSmartArtists()} variant="secondary">
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
                    onClick={() => setStep(4)}
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
                <div className="text-center mt-4">
                  <button
                    onClick={handleSkipToRecommendations}
                    disabled={isSubmitting}
                    className="text-sm text-[var(--text)]/40 hover:text-[var(--text)]/60 transition-colors"
                  >
                    {isSubmitting ? "Loading..." : "Skip artist selection →"}
                  </button>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </main>
  );
}
