"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Fuse from "fuse.js";
import { api } from "@/lib/api";

/**
 * Compact artist entry from the index endpoint.
 * MBID-first: Uses short field names to minimize payload size.
 */
export interface IndexedArtist {
  m: string | null; // mbid (MusicBrainz ID, primary)
  i: string | null; // spotify_id (for images, backward compat)
  n: string; // name
  p: number; // popularity
}

/**
 * Artist search result returned by the hook.
 * MBID-first: MusicBrainz ID is the primary identifier when available.
 */
export interface ArtistSearchResult {
  mbid: string | null; // MusicBrainz ID (primary)
  spotify_id: string | null; // Spotify ID (for images)
  name: string;
  popularity: number;
  // Backward compatibility (deprecated)
  artist_id?: string;
  artist_name?: string;
}

interface UseArtistIndexReturn {
  isLoading: boolean;
  isReady: boolean;
  error: string | null;
  search: (query: string, limit?: number) => ArtistSearchResult[];
  artistCount: number;
}

// Global state to share index across components
let globalIndex: Fuse<IndexedArtist> | null = null;
let globalArtists: IndexedArtist[] = [];
let globalLoadPromise: Promise<void> | null = null;
let globalError: string | null = null;

/**
 * Hook for client-side artist search using a pre-loaded index.
 *
 * The index is loaded once and shared across all components using this hook.
 * Contains ~168K artists with popularity >= 30 for instant fuzzy search.
 */
export function useArtistIndex(): UseArtistIndexReturn {
  const [isLoading, setIsLoading] = useState(!globalIndex);
  const [isReady, setIsReady] = useState(!!globalIndex);
  const [error, setError] = useState<string | null>(globalError);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;

    const loadIndex = async () => {
      // Already loaded
      if (globalIndex) {
        setIsReady(true);
        setIsLoading(false);
        return;
      }

      // Already loading
      if (globalLoadPromise) {
        await globalLoadPromise;
        if (mountedRef.current) {
          setIsReady(!!globalIndex);
          setIsLoading(false);
          setError(globalError);
        }
        return;
      }

      // Start loading
      globalLoadPromise = (async () => {
        // Clear any previous error when starting a new load attempt
        globalError = null;

        try {
          console.log("[ArtistIndex] Loading artist index...");
          const startTime = performance.now();

          const response = await api.catalog.getArtistIndex();
          globalArtists = response.artists;

          // Build Fuse index with fuzzy search config
          globalIndex = new Fuse(globalArtists, {
            keys: ["n"], // Search by name
            threshold: 0.3, // Fuzzy threshold (0 = exact, 1 = match anything)
            distance: 100, // How far to search for a fuzzy match
            minMatchCharLength: 2,
            shouldSort: true,
            // Custom scoring to prefer prefix matches and higher popularity
            sortFn: (a, b) => {
              // First sort by score (lower is better)
              if (a.score !== b.score) {
                return a.score - b.score;
              }
              // Then by popularity (higher is better)
              const aItem = globalArtists[a.idx];
              const bItem = globalArtists[b.idx];
              return (bItem?.p || 0) - (aItem?.p || 0);
            },
          });

          const elapsed = performance.now() - startTime;
          console.log(
            `[ArtistIndex] Loaded ${response.count.toLocaleString()} artists in ${elapsed.toFixed(0)}ms`
          );

          // Explicitly clear error on success
          globalError = null;
        } catch (err) {
          console.error("[ArtistIndex] Failed to load:", err);
          globalError = err instanceof Error ? err.message : "Failed to load artist index";
        }
      })();

      await globalLoadPromise;

      if (mountedRef.current) {
        setIsReady(!!globalIndex);
        setIsLoading(false);
        setError(globalError);
      }
    };

    loadIndex();

    return () => {
      mountedRef.current = false;
    };
  }, []);

  const search = useCallback(
    (query: string, limit: number = 10): ArtistSearchResult[] => {
      if (!globalIndex || !query.trim()) {
        return [];
      }

      const results = globalIndex.search(query.trim(), { limit });

      // MBID-first: Return new format with backward compat aliases
      return results.map((result) => ({
        mbid: result.item.m,
        spotify_id: result.item.i,
        name: result.item.n,
        popularity: result.item.p,
        // Backward compatibility (deprecated)
        artist_id: result.item.i || result.item.m || "",
        artist_name: result.item.n,
      }));
    },
    []
  );

  return {
    isLoading,
    isReady,
    error,
    search,
    artistCount: globalArtists.length,
  };
}
