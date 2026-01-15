"use client";

import { useCallback, useRef, useState } from "react";
import { api } from "@/lib/api";

interface SuggestionReason {
  type: "fans_also_like" | "similar_artist" | "genre_match" | "decade_match" | "popular_choice";
  display_text: string;
  related_to: string | null;
}

/**
 * Quiz artist with MBID-first identifiers.
 */
export interface QuizArtist {
  // Primary identifier (MusicBrainz)
  mbid: string | null;
  name: string;

  // Karaoke catalog data
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;

  // Enrichment (optional)
  spotify_id: string | null;
  genres: string[];
  tags: string[];
  image_url: string | null;
  suggestion_reason: SuggestionReason | null;
}

interface UseInfiniteArtistsParams {
  genres: string[];
  decades: string[];
  manualArtists: { name: string }[];
  enjoySongs: { artist: string }[];
  selectedArtists: Set<string>;
  initialBatchSize?: number;
  loadMoreSize?: number;
}

interface UseInfiniteArtistsReturn {
  artists: QuizArtist[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  error: string | null;
  loadInitial: () => Promise<void>;
  loadMore: () => Promise<void>;
  clearError: () => void;
}

/**
 * Hook for managing infinite scroll artist list.
 *
 * Key behaviors:
 * - Artists already loaded NEVER disappear from the list
 * - New artists are appended to the end
 * - Selected artists are excluded from future fetches
 * - Tracks all shown artists to prevent duplicates
 */
export function useInfiniteArtists({
  genres,
  decades,
  manualArtists,
  enjoySongs,
  selectedArtists,
  initialBatchSize = 15,
  loadMoreSize = 10,
}: UseInfiniteArtistsParams): UseInfiniteArtistsReturn {
  const [artists, setArtists] = useState<QuizArtist[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Track all artists we've ever shown to avoid duplicates
  const shownArtistNames = useRef<Set<string>>(new Set());

  const clearError = useCallback(() => setError(null), []);

  /**
   * Load initial batch of artists
   */
  const loadInitial = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const songArtists = enjoySongs.map((s) => s.artist);

      const response = await api.quiz.getSmartArtists({
        genres: genres.length > 0 ? genres.filter((g) => g !== "other") : undefined,
        decades: decades.length > 0 ? decades : undefined,
        manual_artists: manualArtists.length > 0 ? manualArtists.map((a) => a.name) : undefined,
        manual_song_artists: songArtists.length > 0 ? songArtists : undefined,
        exclude: Array.from(selectedArtists),
        count: initialBatchSize,
      });

      // Track shown artists
      response.artists.forEach((a) => shownArtistNames.current.add(a.name));

      setArtists(response.artists);
      setHasMore(response.has_more);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load artists");
    } finally {
      setIsLoading(false);
    }
  }, [genres, decades, manualArtists, enjoySongs, selectedArtists, initialBatchSize]);

  /**
   * Load more artists (for infinite scroll)
   */
  const loadMore = useCallback(async () => {
    if (isLoadingMore || !hasMore) return;

    try {
      setIsLoadingMore(true);
      setError(null);

      const songArtists = enjoySongs.map((s) => s.artist);

      // Exclude all artists we've already shown PLUS selected artists
      const allExcluded = new Set([
        ...Array.from(shownArtistNames.current),
        ...Array.from(selectedArtists),
      ]);

      const response = await api.quiz.getSmartArtists({
        genres: genres.length > 0 ? genres.filter((g) => g !== "other") : undefined,
        decades: decades.length > 0 ? decades : undefined,
        manual_artists: manualArtists.length > 0 ? manualArtists.map((a) => a.name) : undefined,
        manual_song_artists: songArtists.length > 0 ? songArtists : undefined,
        exclude: Array.from(allExcluded),
        count: loadMoreSize,
      });

      // Filter out any duplicates that somehow got through
      const newArtists = response.artists.filter(
        (a) => !shownArtistNames.current.has(a.name)
      );

      // Track new shown artists
      newArtists.forEach((a) => shownArtistNames.current.add(a.name));

      // Append to existing list
      setArtists((prev) => [...prev, ...newArtists]);
      setHasMore(response.has_more && newArtists.length > 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load more artists");
    } finally {
      setIsLoadingMore(false);
    }
  }, [genres, decades, manualArtists, enjoySongs, selectedArtists, isLoadingMore, hasMore, loadMoreSize]);

  return {
    artists,
    isLoading,
    isLoadingMore,
    hasMore,
    error,
    loadInitial,
    loadMore,
    clearError,
  };
}
