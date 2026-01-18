"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { QuizArtistCard, ArtistAffinity } from "@/components/QuizArtistCard";
import { StickyFinishBar } from "@/components/StickyFinishBar";
import { SongSearchAutocomplete, SelectedSong } from "@/components/SongSearchAutocomplete";
import { ArtistSearchAutocomplete, SelectedArtist } from "@/components/ArtistSearchAutocomplete";
import { useQuizDraft } from "@/hooks/useQuizDraft";
import { EnjoySingingModal, EnjoySingingMetadataResult } from "@/components/EnjoySingingModal";
import { CheckIcon, ChevronRightIcon, MicrophoneIcon, XIcon, LoaderIcon, LastfmIcon, SpotifyIcon } from "@/components/icons";
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

// 6-step quiz (redesigned flow):
// Step 1: How It Works (intro) - explains the quiz, sets expectations
// Step 2: What Kind of Music? (genres required + decades optional combined)
// Step 3: Artists You Know (manual entry) - triggers smart artist pre-calc on continue
// Step 4: Karaoke Preferences (prefs + songs you love to sing) - buys time for pre-calc
// Step 5: Know Any of These? (smart suggestions) - loads instantly from pre-calc
// Step 6: Email + Generating (collect email while recommendations generate)
type QuizStep = 1 | 2 | 3 | 4 | 5 | 6;

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

  // Draft persistence hook
  const { draft, saveDraft, clearDraft } = useQuizDraft();
  const hasRestoredDraft = useRef(false);

  const [step, setStep] = useState<QuizStep>(1);
  const [quizArtists, setQuizArtists] = useState<QuizArtist[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // User selections
  const [selectedGenres, setSelectedGenres] = useState<Set<string>>(new Set());
  const [selectedDecades, setSelectedDecades] = useState<Set<string>>(new Set());
  // Artist affinity: Map of artist name -> affinity level (occasionally/like/love)
  const [artistAffinity, setArtistAffinity] = useState<Map<string, ArtistAffinity>>(new Map());
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
  const [isGenerating, setIsGenerating] = useState(false);
  const [recommendationsReady, setRecommendationsReady] = useState(false);
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [isEmailSubmitting, setIsEmailSubmitting] = useState(false);
  const [emailSubmitted, setEmailSubmitted] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMoreArtists, setHasMoreArtists] = useState(true);

  // Refs for infinite scroll
  const loadMoreTriggerRef = useRef<HTMLDivElement>(null);
  const shownArtistNamesRef = useRef<Set<string>>(new Set());

  // Restore state from draft on mount (only once)
  // draft: undefined = loading, null = no draft, QuizDraft = has draft
  useEffect(() => {
    // Still loading from localStorage, wait
    if (draft === undefined) return;

    // Already restored, skip
    if (hasRestoredDraft.current) return;

    // Mark as restored (whether we have a draft or not)
    hasRestoredDraft.current = true;

    // If we have a draft, restore the state
    if (draft) {
      setStep(draft.step);
      setSelectedGenres(new Set(draft.selectedGenres));
      setSelectedDecades(new Set(draft.selectedDecades));
      setArtistAffinity(new Map(Object.entries(draft.artistAffinity)));
      setManualArtists(draft.manualArtists);
      setEnjoySongs(draft.enjoySongs as EnjoySongSelection[]);
      setEnergyPreference(draft.energyPreference);
      setVocalComfortPref(draft.vocalComfortPref);
      setCrowdPleaserPref(draft.crowdPleaserPref);
    }
  }, [draft]);

  // Save draft when state changes (after initial restore)
  useEffect(() => {
    // Don't save until we've restored (or confirmed no draft exists)
    if (!hasRestoredDraft.current) return;

    saveDraft({
      step,
      selectedGenres: Array.from(selectedGenres),
      selectedDecades: Array.from(selectedDecades),
      artistAffinity: Object.fromEntries(artistAffinity),
      manualArtists,
      enjoySongs,
      energyPreference,
      vocalComfortPref,
      crowdPleaserPref,
    });
  }, [
    step,
    selectedGenres,
    selectedDecades,
    artistAffinity,
    manualArtists,
    enjoySongs,
    energyPreference,
    vocalComfortPref,
    crowdPleaserPref,
    saveDraft,
  ]);

  // Pre-loading state: load artists based on genres/decades while user is on step 4
  const preloadedArtistsRef = useRef<QuizArtist[] | null>(null);
  const preloadHasMoreRef = useRef<boolean>(true);
  const isPreloadingRef = useRef<boolean>(false);

  // Pre-load artists based on genres/decades/manual artists (called when exiting step 3)
  // This allows step 5 to load instantly since step 4 buys time
  const preloadArtists = useCallback(async () => {
    // Don't preload if already preloading or already have data
    if (isPreloadingRef.current || preloadedArtistsRef.current !== null) return;

    isPreloadingRef.current = true;
    try {
      const response = await api.quiz.getSmartArtists({
        genres: selectedGenres.size > 0 ? Array.from(selectedGenres).filter(g => g !== "other") : undefined,
        decades: selectedDecades.size > 0 ? Array.from(selectedDecades) : undefined,
        // Include manual artists for better "fans also like" suggestions
        manual_artists: manualArtists.length > 0 ? manualArtists.map(a => a.name) : undefined,
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
  }, [selectedGenres, selectedDecades, manualArtists]);

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
  // But not if we're on step 6 (email collection + generating)
  useEffect(() => {
    if (!authLoading && !quizStatusLoading && isAuthenticated && hasCompletedQuiz && step !== 6) {
      router.push("/recommendations");
    }
  }, [authLoading, quizStatusLoading, isAuthenticated, hasCompletedQuiz, step, router]);

  // Scroll to top when step changes
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [step]);

  // Handler for continuing from step 3 - triggers preload and advances
  const handleContinueFromStep3 = () => {
    preloadArtists();
    setStep(4);
  };

  // Load smart artists when entering step 5 (uses pre-loaded data if available)
  useEffect(() => {
    if (isAuthenticated && step === 5) {
      loadSmartArtists();
    }
  }, [isAuthenticated, step, loadSmartArtists]);

  const handleArtistAffinityChange = (artistName: string, affinity: ArtistAffinity | null) => {
    setArtistAffinity((prev) => {
      const next = new Map(prev);
      if (affinity === null) {
        next.delete(artistName);
      } else {
        next.set(artistName, affinity);
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

      // Exclude all artists we've shown + artists with affinity set
      const allExcluded = new Set([
        ...Array.from(shownArtistNamesRef.current),
        ...Array.from(artistAffinity.keys()),
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
  }, [isLoadingMore, hasMoreArtists, selectedGenres, selectedDecades, manualArtists, enjoySongs, artistAffinity]);

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

      // Convert artist affinity Map to array format for API
      const artistAffinityArray = Array.from(artistAffinity.entries()).map(
        ([artist_name, affinity]) => ({ artist_name, affinity })
      );

      // Move to step 6 immediately to show generating state
      setStep(6);
      setIsSubmitting(false);
      setIsGenerating(true);

      // Submit main quiz data with all new fields
      await api.quiz.submit({
        artist_affinities: artistAffinityArray,
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

      // Clear the saved draft since quiz is complete
      clearDraft();

      // Mark recommendations as ready
      setIsGenerating(false);
      setRecommendationsReady(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit quiz");
      setIsSubmitting(false);
      setIsGenerating(false);
    }
  };

  const handleEmailSubmit = async () => {
    // Validate email
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!email.trim()) {
      setEmailError("Please enter your email");
      return;
    }
    if (!emailRegex.test(email.trim())) {
      setEmailError("Please enter a valid email");
      return;
    }

    try {
      setIsEmailSubmitting(true);
      setEmailError(null);
      // Collect email
      await api.auth.collectEmail(email.trim());
      setEmailSubmitted(true);
      setIsEmailSubmitting(false);
    } catch (err) {
      setIsEmailSubmitting(false);
      setEmailError(err instanceof Error ? err.message : "Failed to save email");
    }
  };

  const handleViewRecommendations = () => {
    router.push("/recommendations");
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
  // But not if we're on step 6 (email collection + generating)
  if (authLoading || quizStatusLoading || !isAuthenticated || (hasCompletedQuiz && step !== 6)) {
    return <LoadingOverlay message="Starting quiz..." />;
  }

  return (
    <main className="min-h-screen pb-safe">
      <div className="max-w-2xl mx-auto px-4 py-6">
        {/* Progress indicator */}
        <div data-testid="progress-indicator" className="flex items-center justify-center gap-2 mb-8">
          {[1, 2, 3, 4, 5, 6].map((s) => (
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

        {/* Step 1: How It Works (Intro) */}
        {step === 1 && (
          <>
            <div className="text-center mb-8">
              <div className="text-5xl mb-4">🎤</div>
              <h1 data-testid="quiz-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Let&apos;s find your perfect karaoke songs
              </h1>
              <p className="text-[var(--text)]/60">
                Answer a few quick questions so we can personalize your recommendations.
              </p>
            </div>

            {/* Info bullets */}
            <div className="space-y-4 mb-8">
              <div className="flex items-start gap-4 p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
                <span className="text-2xl flex-shrink-0">✨</span>
                <div>
                  <h3 className="font-semibold text-[var(--text)] mb-1">Tell us about your music taste</h3>
                  <p className="text-sm text-[var(--text)]/60">
                    We&apos;ll ask about genres, decades, and artists you like.
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-4 p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
                <span className="text-2xl flex-shrink-0">📊</span>
                <div>
                  <h3 className="font-semibold text-[var(--text)] mb-1">More info = better recommendations</h3>
                  <p className="text-sm text-[var(--text)]/60">
                    The more you share, the better we can personalize your song suggestions.
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-4 p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
                <span className="text-2xl flex-shrink-0">⏭️</span>
                <div>
                  <h3 className="font-semibold text-[var(--text)] mb-1">Skip if you&apos;re in a hurry</h3>
                  <p className="text-sm text-[var(--text)]/60">
                    All questions after genres are optional - skip ahead anytime.
                  </p>
                </div>
              </div>
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
                Get Started
              </Button>
            </div>
          </>
        )}

        {/* Step 2: What Kind of Music (Genres + Decades combined) */}
        {step === 2 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="music-taste-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                What kind of music do you listen to?
              </h1>
              <p className="text-[var(--text)]/60">
                Select your favorite decades and genres to get started.
              </p>
            </div>

            {/* Decades section */}
            <div data-testid="decade-section" className="grid grid-cols-4 gap-2 mb-6">
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
            <div className="text-center mb-6">
              <span data-testid="genre-selection-count" className="text-[var(--text)]/40 text-sm">
                {selectedGenres.size === 0
                  ? "Select at least one genre"
                  : `${selectedGenres.size} genre${selectedGenres.size > 1 ? "s" : ""} selected`}
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
                disabled={selectedGenres.size === 0}
                rightIcon={<ChevronRightIcon className="w-5 h-5" />}
              >
                Continue
              </Button>
            </div>

            {/* Skip link - only if genres selected */}
            {selectedGenres.size > 0 && (
              <div className="text-center mt-4">
                <button
                  onClick={handleSkipToRecommendations}
                  disabled={isSubmitting}
                  className="text-sm text-[var(--text)]/40 hover:text-[var(--text)]/60 transition-colors"
                >
                  {isSubmitting ? "Loading..." : "Skip to recommendations →"}
                </button>
              </div>
            )}
          </>
        )}

        {/* Step 3: Artists You Know (Manual Entry - triggers pre-calc on continue) */}
        {step === 3 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="artists-you-know-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Artists You Know
              </h1>
              <p className="text-[var(--text)]/60">
                The more artists you add, the better your recommendations.
              </p>
            </div>

            {/* Manual Artist Entry */}
            <div className="mb-6">
              <ArtistSearchAutocomplete
                onSelect={handleAddManualArtist}
                selectedArtistIds={new Set(manualArtists.map((a) => getArtistUniqueId(a)))}
                placeholder="Search for artists you know..."
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

            {/* Artist count */}
            {manualArtists.length > 0 && (
              <div className="text-center mb-6">
                <span className="text-[var(--text)]/40 text-sm">
                  {manualArtists.length} artist{manualArtists.length > 1 ? "s" : ""} added
                </span>
              </div>
            )}

            {/* Import listening history CTA */}
            <div className="mb-6 p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
              <div className="flex items-start gap-3">
                <span className="text-2xl flex-shrink-0">📥</span>
                <div>
                  <h2 className="font-semibold text-[var(--text)] mb-1">
                    Have listening history in Last.fm or Spotify?
                  </h2>
                  <p className="text-[var(--text)]/60 text-sm mb-3">
                    Import it for even better recommendations!
                  </p>
                  <div className="space-y-2 text-sm">
                    <div className="flex items-center gap-2 text-[var(--text)]/50">
                      <LastfmIcon className="w-4 h-4 text-[#d51007] flex-shrink-0" />
                      <span><span className="text-[var(--text)]/70">Last.fm</span> — direct import</span>
                    </div>
                    <div className="flex items-center gap-2 text-[var(--text)]/50">
                      <SpotifyIcon className="w-4 h-4 text-[#1DB954] flex-shrink-0" />
                      <span><span className="text-[var(--text)]/70">ListenBrainz</span> — imports from Spotify</span>
                    </div>
                  </div>
                  <p className="text-[var(--text)]/40 text-xs mt-3">
                    After the quiz, go to Settings → Connected Services to sync.
                  </p>
                </div>
              </div>
            </div>

            {/* Hint when empty */}
            {manualArtists.length === 0 && (
              <div className="text-center py-4 mb-6">
                <p className="text-[var(--text)]/40 text-sm">
                  Adding artists helps us find songs you&apos;ll love to sing.
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
                onClick={() => setStep(2)}
              >
                Back
              </Button>
              <Button
                variant="primary"
                size="lg"
                className="flex-1"
                onClick={handleContinueFromStep3}
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

        {/* Step 4: Karaoke Preferences + Songs You Love to Sing */}
        {step === 4 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="preferences-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Karaoke Preferences
              </h1>
              <p className="text-[var(--text)]/60">
                Help us understand your karaoke style.
              </p>
            </div>

            {/* Songs I Enjoy Singing */}
            <div className="mb-6">
              <h2 className="text-sm font-medium text-[var(--text)]/70 mb-2 uppercase tracking-wide">
                Songs You Love to Sing
              </h2>
              <p className="text-[var(--text)]/50 text-sm mb-3">
                Add songs you&apos;ve sung before and enjoyed and tell us why!
              </p>
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
                  <button
                    onClick={() => setEnjoySongs([])}
                    className="text-sm text-[var(--text)]/40 hover:text-[var(--text)] transition-colors"
                  >
                    Clear all
                  </button>
                </div>
                <div className="space-y-2">
                  {enjoySongs.map((song) => (
                    <div
                      key={song.song_id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)]"
                    >
                      <div className="flex-1 min-w-0">
                        <span className="text-[var(--text)] font-medium truncate block">
                          {song.title}
                        </span>
                        <span className="text-[var(--text-muted)] text-sm truncate block">
                          {song.artist}
                        </span>
                      </div>
                      <button
                        onClick={() => handleEditEnjoySong(song)}
                        className={`
                          px-3 py-1.5 rounded-lg text-sm font-medium transition-colors
                          ${
                            song.singing_tags && song.singing_tags.length > 0
                              ? "bg-[var(--brand-pink)]/20 text-[var(--brand-pink)]"
                              : "border border-[var(--brand-pink)] text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/10"
                          }
                        `}
                      >
                        {song.singing_tags && song.singing_tags.length > 0 ? "Edit" : "Why?"}
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
            <div data-testid="crowd-pleaser-section" className="mb-6">
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

        {/* Step 5: Know Any of These? (Smart Suggestions - final step, loads from pre-calc) */}
        {step === 5 && (
          <>
            <div className="text-center mb-8">
              <h1 data-testid="smart-artists-heading" className="text-2xl font-bold text-[var(--text)] mb-2">
                Know Any of These Artists?
              </h1>
              <p className="text-[var(--text)]/60">
                Based on your preferences, you might know these artists.
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
                    {artistAffinity.size} selected
                  </span>
                  {artistAffinity.size > 0 && (
                    <button
                      onClick={() => setArtistAffinity(new Map())}
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
                      affinity={artistAffinity.get(artist.name) ?? null}
                      onAffinityChange={(newAffinity) => handleArtistAffinityChange(artist.name, newAffinity)}
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

                {/* Sticky finish bar with back button */}
                <StickyFinishBar
                  selectedCount={artistAffinity.size}
                  onFinish={handleSubmit}
                  onSkip={handleSkipToRecommendations}
                  onBack={() => setStep(4)}
                  isSubmitting={isSubmitting}
                />
              </>
            )}
          </>
        )}

        {/* Step 6: Email Collection + Generating Recommendations */}
        {step === 6 && (
          <>
            <div className="text-center mb-8">
              <h1 className="text-2xl font-bold text-[var(--text)] mb-2">
                Almost There!
              </h1>
              <p className="text-[var(--text)]/60">
                Enter your email to save your recommendations.
              </p>
            </div>

            {/* Generation Progress */}
            <div className="mb-8 p-6 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
              <div className="flex items-center gap-4">
                {isGenerating ? (
                  <>
                    <LoaderIcon className="w-8 h-8 animate-spin text-[var(--brand-pink)] flex-shrink-0" />
                    <div className="flex-1">
                      <h3 className="font-semibold text-[var(--text)]">Generating your recommendations...</h3>
                      <p className="text-sm text-[var(--text-muted)]">Analyzing your music taste</p>
                      {/* Progress bar */}
                      <div className="mt-3 h-2 bg-[var(--secondary)] rounded-full overflow-hidden">
                        <div className="h-full bg-[var(--brand-pink)] rounded-full animate-pulse" style={{ width: "60%" }} />
                      </div>
                    </div>
                  </>
                ) : recommendationsReady ? (
                  <>
                    <div className="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center flex-shrink-0">
                      <CheckIcon className="w-5 h-5 text-green-500" />
                    </div>
                    <div className="flex-1">
                      <h3 className="font-semibold text-[var(--text)]">Recommendations ready!</h3>
                      <p className="text-sm text-[var(--text-muted)]">Your personalized song list is waiting</p>
                      {/* Completed progress bar */}
                      <div className="mt-3 h-2 bg-[var(--secondary)] rounded-full overflow-hidden">
                        <div className="h-full bg-green-500 rounded-full" style={{ width: "100%" }} />
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="w-8 h-8 rounded-full bg-[var(--secondary)] flex items-center justify-center flex-shrink-0">
                      <span className="text-[var(--text-muted)]">⏳</span>
                    </div>
                    <div className="flex-1">
                      <h3 className="font-semibold text-[var(--text)]">Waiting to generate...</h3>
                      <p className="text-sm text-[var(--text-muted)]">This will start shortly</p>
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Email Input */}
            <div className="mb-8">
              <label htmlFor="email" className="block text-sm font-medium text-[var(--text)]/70 mb-2 uppercase tracking-wide">
                Your Email
              </label>
              {emailSubmitted ? (
                <div className="flex items-center gap-3 p-4 rounded-xl bg-green-500/10 border border-green-500/30">
                  <CheckIcon className="w-5 h-5 text-green-500 flex-shrink-0" />
                  <div>
                    <p className="text-[var(--text)] font-medium">Email saved!</p>
                    <p className="text-sm text-[var(--text-muted)]">{email}</p>
                  </div>
                </div>
              ) : (
                <>
                  <div className="flex gap-3">
                    <input
                      id="email"
                      type="email"
                      value={email}
                      onChange={(e) => {
                        setEmail(e.target.value);
                        setEmailError(null);
                      }}
                      placeholder="you@example.com"
                      className={`
                        flex-1 px-4 py-3 rounded-xl bg-[var(--card)] border text-[var(--text)]
                        placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--brand-pink)]
                        ${emailError ? "border-red-500" : "border-[var(--card-border)]"}
                      `}
                      disabled={isEmailSubmitting}
                    />
                    <Button
                      variant="secondary"
                      onClick={handleEmailSubmit}
                      disabled={isEmailSubmitting || !email.trim()}
                    >
                      {isEmailSubmitting ? "Saving..." : "Save"}
                    </Button>
                  </div>
                  {emailError && (
                    <p className="mt-2 text-sm text-red-500">{emailError}</p>
                  )}
                  <p className="mt-3 text-xs text-[var(--text-muted)]">
                    We&apos;ll send you updates about your recommendations. No spam, ever.
                  </p>
                </>
              )}
            </div>

            {/* View Recommendations Button */}
            <Button
              variant="primary"
              size="lg"
              className="w-full"
              onClick={handleViewRecommendations}
              disabled={!emailSubmitted || !recommendationsReady}
              rightIcon={<ChevronRightIcon className="w-5 h-5" />}
            >
              {!recommendationsReady
                ? "Generating recommendations..."
                : !emailSubmitted
                  ? "Enter your email to continue"
                  : "View My Recommendations"}
            </Button>

            {!emailSubmitted && recommendationsReady && (
              <p className="text-center mt-3 text-sm text-[var(--text-muted)]">
                Please enter your email above to see your recommendations
              </p>
            )}
          </>
        )}
      </div>
    </main>
  );
}
