"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { QuizArtistCard } from "@/components/QuizArtistCard";
import { StickyFinishBar } from "@/components/StickyFinishBar";
import { SongSearchAutocomplete, SelectedSong } from "@/components/SongSearchAutocomplete";
import { ArtistSearchAutocomplete, SelectedArtist } from "@/components/ArtistSearchAutocomplete";
import { EnjoySingingModal, EnjoySingingMetadataResult } from "@/components/EnjoySingingModal";
import { SparklesIcon, CheckIcon, ChevronRightIcon, MicrophoneIcon, XIcon, LoaderIcon, LastfmIcon, SpotifyIcon } from "@/components/icons";
import { Button, LoadingPulse, LoadingOverlay } from "@/components/ui";
import type { SingingTag, SingingEnergy, VocalComfort } from "@/types";

interface SuggestionReason {
  type: "fans_also_like" | "similar_artist" | "genre_match" | "decade_match" | "popular_choice";
  display_text: string;
  related_to: string | null;
}

/**
 * Quiz artist with MBID-first identifiers.
 * MBID is the primary identifier when available.
 */
interface QuizArtist {
  // Primary identifier (MusicBrainz)
  mbid?: string | null;
  name: string;

  // Karaoke catalog data
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;

  // Enrichment (optional)
  spotify_id?: string | null;
  genres?: string[];
  tags?: string[];
  image_url: string | null;
  suggestion_reason?: SuggestionReason | null;
}

/** Helper to get unique ID for an artist (MBID-first) */
function getArtistUniqueId(artist: QuizArtist | SelectedArtist): string {
  if ("mbid" in artist && artist.mbid) return artist.mbid;
  if ("spotify_id" in artist && artist.spotify_id) return artist.spotify_id;
  // SelectedArtist has artist_id for backward compat
  if ("artist_id" in artist && artist.artist_id) return artist.artist_id;
  return artist.name;
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
  const [hasMoreArtists, setHasMoreArtists] = useState(true);

  // Refs for infinite scroll
  const loadMoreTriggerRef = useRef<HTMLDivElement>(null);
  const shownArtistNamesRef = useRef<Set<string>>(new Set());

  // Pre-loading state: load artists based on genres/decades while user is on step 4
  const preloadedArtistsRef = useRef<QuizArtist[] | null>(null);
  const preloadHasMoreRef = useRef<boolean>(true);
  const isPreloadingRef = useRef<boolean>(false);

  // Pre-load artists based on genres/decades only (called when entering step 4)
  // This allows step 5 to load instantly
  const preloadArtists = useCallback(async () => {
    // Don't preload if already preloading or already have data
    if (isPreloadingRef.current || preloadedArtistsRef.current !== null) return;

    isPreloadingRef.current = true;
    try {
      const response = await api.quiz.getSmartArtists({
        genres: selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined,
        decades: selectedDecades.size > 0 ? Array.from(selectedDecades) : undefined,
        // Don't include manual artists/songs - those will be used for subsequent loads
        count: 50,
      });

      preloadedArtistsRef.current = response.artists;
      preloadHasMoreRef.current = response.has_more;
    } catch (err) {
      console.error("Pre-load failed:", err);
      // That's okay, we'll load fresh when entering step 5
    } finally {
      isPreloadingRef.current = false;
    }
  }, [selectedGenres, selectedDecades]);

  // Load smart artists for step 5 - uses pre-loaded data if available
  const loadSmartArtists = useCallback(async () => {
    // Reset tracking
    shownArtistNamesRef.current = new Set();

    // Use pre-loaded data if available (instant!)
    if (preloadedArtistsRef.current !== null) {
      preloadedArtistsRef.current.forEach((a) => shownArtistNamesRef.current.add(a.name));
      setQuizArtists(preloadedArtistsRef.current);
      setHasMoreArtists(preloadHasMoreRef.current);
      // Clear pre-loaded data so we don't reuse stale data
      preloadedArtistsRef.current = null;
      return;
    }

    // Otherwise load fresh (fallback if pre-load didn't happen)
    try {
      setIsLoading(true);
      setError(null);

      const response = await api.quiz.getSmartArtists({
        genres: selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined,
        decades: selectedDecades.size > 0 ? Array.from(selectedDecades) : undefined,
        // Initial load doesn't include manual artists - those are for subsequent loads
        count: 50,
      });

      // Track shown artists
      response.artists.forEach((a) => shownArtistNamesRef.current.add(a.name));

      setQuizArtists(response.artists);
      setHasMoreArtists(response.has_more);
    } catch (err) {
      // Fallback to regular artist loading if smart endpoint fails
      console.error("Smart artist loading failed, falling back:", err);
      try {
        const genresArray = selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined;
        const response = await api.quiz.getArtists(50, genresArray);
        response.artists.forEach((a) => shownArtistNamesRef.current.add(a.name));
        setQuizArtists(response.artists);
        setHasMoreArtists(true); // Assume there's more in fallback mode
      } catch (fallbackErr) {
        setError(fallbackErr instanceof Error ? fallbackErr.message : "Failed to load artists");
      }
    } finally {
      setIsLoading(false);
    }
  }, [selectedGenres, selectedDecades]);

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

  // Pre-load artists when entering step 4 (so step 5 loads instantly)
  useEffect(() => {
    if (isAuthenticated && step === 4) {
      preloadArtists();
    }
  }, [isAuthenticated, step, preloadArtists]);

  // Load smart artists when entering step 5 (uses pre-loaded data if available)
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

  const handleLoadMoreArtists = useCallback(async () => {
    if (isLoadingMore || !hasMoreArtists) return;

    try {
      setIsLoadingMore(true);
      const songArtists = enjoySongs.map((s) => s.artist);

      // Exclude all artists we've shown + selected artists
      const allExcluded = new Set([
        ...Array.from(shownArtistNamesRef.current),
        ...Array.from(selectedArtists),
      ]);

      const response = await api.quiz.getSmartArtists({
        genres: selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined,
        decades: selectedDecades.size > 0 ? Array.from(selectedDecades) : undefined,
        manual_artists: manualArtists.length > 0 ? manualArtists.map(a => a.name) : undefined,
        manual_song_artists: songArtists.length > 0 ? songArtists : undefined,
        exclude: Array.from(allExcluded),
        count: 50,
      });

      // Filter out any duplicates that somehow got through
      const newArtists = response.artists.filter(
        (a) => !shownArtistNamesRef.current.has(a.name)
      );

      // Track new shown artists
      newArtists.forEach((a) => shownArtistNamesRef.current.add(a.name));

      // Append to existing list (never replace!)
      if (newArtists.length > 0) {
        setQuizArtists((prev) => [...prev, ...newArtists]);
      }

      setHasMoreArtists(response.has_more && newArtists.length > 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more artists");
    } finally {
      setIsLoadingMore(false);
    }
  }, [isLoadingMore, hasMoreArtists, selectedGenres, selectedDecades, manualArtists, enjoySongs, selectedArtists]);

  // Infinite scroll: Observe when user scrolls near bottom to load more
  useEffect(() => {
    if (step !== 5) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting && hasMoreArtists && !isLoadingMore && !isLoading) {
          handleLoadMoreArtists();
        }
      },
      {
        rootMargin: "800px", // Trigger 800px before reaching the element (start loading early)
        threshold: 0,
      }
    );

    const trigger = loadMoreTriggerRef.current;
    if (trigger) {
      observer.observe(trigger);
    }

    return () => {
      if (trigger) {
        observer.unobserve(trigger);
      }
    };
  }, [step, hasMoreArtists, isLoadingMore, isLoading, handleLoadMoreArtists]);

  const handleSubmit = async () => {
    try {
      setIsSubmitting(true);

      // Map manual artists to API format (MBID-first)
      const mappedManualArtists = manualArtists.map((a) => ({
        mbid: a.mbid,
        artist_id: a.spotify_id || a.artist_id, // Spotify ID for backward compat
        artist_name: a.name,
        genres: a.genres,
      }));

      // Submit main quiz data with all new fields
      await api.quiz.submit({
        known_artists: Array.from(selectedArtists),
        decade_preferences: Array.from(selectedDecades),
        energy_preference: energyPreference,
        genres: Array.from(selectedGenres),
        vocal_comfort_pref: vocalComfortPref,
        crowd_pleaser_pref: crowdPleaserPref,
        manual_artists: mappedManualArtists,
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

  // Step 4: Manual artist handlers (MBID-first unique ID)
  const handleAddManualArtist = (artist: SelectedArtist) => {
    const newId = getArtistUniqueId(artist);
    // Don't add duplicates
    if (!manualArtists.some((a) => getArtistUniqueId(a) === newId)) {
      setManualArtists((prev) => [...prev, artist]);
    }
  };

  const handleRemoveManualArtist = (artistUniqueId: string) => {
    setManualArtists((prev) => prev.filter((a) => getArtistUniqueId(a) !== artistUniqueId));
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
                selectedArtistIds={new Set(manualArtists.map((a) => getArtistUniqueId(a)))}
                placeholder="Search for artists you like..."
                className="mb-3"
              />
              {manualArtists.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {manualArtists.map((artist) => {
                    const uniqueId = getArtistUniqueId(artist);
                    return (
                      <span
                        key={uniqueId}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full bg-[var(--brand-pink)]/20 text-[var(--brand-pink)] text-sm font-medium"
                      >
                        {artist.name}
                        <button
                          onClick={() => handleRemoveManualArtist(uniqueId)}
                          className="hover:text-[var(--text)] transition-colors"
                        >
                          <XIcon className="w-3 h-3" />
                        </button>
                      </span>
                    );
                  })}
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

            {/* Import listening history info */}
            <div className="mb-6 p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-2 uppercase tracking-wide">
                Have listening history?
              </h2>
              <p className="text-[var(--text)]/60 text-sm mb-3">
                After completing the quiz, you can import your listening history from:
              </p>
              <ul className="text-[var(--text)]/50 text-sm space-y-2">
                <li className="flex items-center gap-2">
                  <LastfmIcon className="w-4 h-4 text-[#d51007] flex-shrink-0" />
                  <span><span className="text-[var(--text)]/70">Last.fm</span> — direct import</span>
                </li>
                <li className="flex items-center gap-2">
                  <SpotifyIcon className="w-4 h-4 text-[#1DB954] flex-shrink-0" />
                  <span><span className="text-[var(--text)]/70">ListenBrainz</span> — imports from Spotify, Libre.fm, PanoScrobbler, and Maloja</span>
                </li>
              </ul>
              <p className="text-[var(--text)]/40 text-xs mt-3">
                Go to Settings → Connected Services after creating an account.
              </p>
            </div>

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
                {/* Selection count and clear button */}
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

                {/* Artist grid with infinite scroll */}
                <div
                  data-testid="artist-grid"
                  className="grid grid-cols-1 gap-3"
                  style={{ paddingBottom: "120px" }} // Space for sticky bar
                >
                  {quizArtists.map((artist, index) => (
                    <QuizArtistCard
                      key={artist.name}
                      artist={artist}
                      isSelected={selectedArtists.has(artist.name)}
                      onToggle={() => toggleArtist(artist.name)}
                      index={index}
                    />
                  ))}

                  {/* Infinite scroll trigger */}
                  <div
                    ref={loadMoreTriggerRef}
                    className="flex items-center justify-center py-4"
                  >
                    {isLoadingMore && (
                      <div className="flex items-center gap-2 text-[var(--text-muted)]">
                        <LoaderIcon className="w-4 h-4 animate-spin" />
                        <span className="text-sm">Finding more artists you may know...</span>
                      </div>
                    )}
                    {!hasMoreArtists && quizArtists.length > 0 && (
                      <p className="text-sm text-[var(--text-muted)]">
                        No more artists to show
                      </p>
                    )}
                  </div>
                </div>

                {/* Sticky finish bar */}
                <StickyFinishBar
                  selectedCount={selectedArtists.size}
                  onFinish={handleSubmit}
                  onSkip={handleSkipToRecommendations}
                  isSubmitting={isSubmitting}
                />

                {/* Back button - fixed in corner for easy access */}
                <button
                  onClick={() => setStep(4)}
                  className="fixed bottom-20 left-4 z-50 p-2 rounded-full bg-[var(--secondary)] border border-[var(--card-border)] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  title="Go back"
                >
                  <ChevronRightIcon className="w-5 h-5 transform rotate-180" />
                </button>
              </>
            )}
          </>
        )}
      </div>
    </main>
  );
}
