"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import type { ArtistAffinity } from "@/components/QuizArtistCard";
import type { SelectedArtist } from "@/components/ArtistSearchAutocomplete";

const STORAGE_KEY = "quiz_draft_v1";
const DEBOUNCE_MS = 3000;

export type QuizStep = 1 | 2 | 3 | 4 | 5 | 6;
export type EnergyPreference = "chill" | "medium" | "high" | null;
export type VocalComfortPref = "easy" | "challenging" | "any" | null;
export type CrowdPleaserPref = "hits" | "deep_cuts" | "any" | null;

export interface EnjoySongSelection {
  song_id: string;
  artist: string;
  title: string;
  singing_tags?: string[];
  singing_energy?: string | null;
  vocal_comfort?: string | null;
  notes?: string | null;
}

/**
 * Serializable draft format for localStorage
 */
export interface QuizDraft {
  step: QuizStep;
  selectedGenres: string[];
  selectedDecades: string[];
  artistAffinity: Record<string, ArtistAffinity>; // Serializable format
  manualArtists: SelectedArtist[];
  enjoySongs: EnjoySongSelection[];
  energyPreference: EnergyPreference;
  vocalComfortPref: VocalComfortPref;
  crowdPleaserPref: CrowdPleaserPref;
  lastSaved: number; // timestamp
}

export interface UseQuizDraftReturn {
  draft: QuizDraft | null;
  saveDraft: (draft: Partial<QuizDraft>) => void;
  clearDraft: () => void;
  isSyncing: boolean;
  lastSyncError: Error | null;
  isOnline: boolean;
}

/**
 * Hook for auto-saving quiz progress to localStorage and backend.
 *
 * - Saves to localStorage immediately on every change
 * - Debounced sync to backend every 3 seconds of inactivity
 * - Shows toast on sync failure (without interrupting flow)
 * - On page load, restores from localStorage if exists
 * - Clear localStorage on successful quiz submit
 */
export function useQuizDraft(): UseQuizDraftReturn {
  const [draft, setDraft] = useState<QuizDraft | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [lastSyncError, setLastSyncError] = useState<Error | null>(null);
  const [isOnline, setIsOnline] = useState(true);

  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pendingDraftRef = useRef<QuizDraft | null>(null);
  const isOnlineRef = useRef(true);

  // Initialize from localStorage on mount
  useEffect(() => {
    if (typeof window === "undefined") return;

    // Check online status
    const online = navigator.onLine;
    setIsOnline(online);
    isOnlineRef.current = online;

    // Load draft from localStorage
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as QuizDraft;
        setDraft(parsed);
      }
    } catch (err) {
      console.warn("Failed to load quiz draft from localStorage:", err);
    }

    // Listen for online/offline events
    const handleOnline = () => {
      setIsOnline(true);
      isOnlineRef.current = true;
      // Trigger sync on reconnect if there's a pending draft
      if (pendingDraftRef.current) {
        syncToBackend(pendingDraftRef.current);
      }
    };
    const handleOffline = () => {
      setIsOnline(false);
      isOnlineRef.current = false;
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      // Clear any pending debounce timer
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);

  /**
   * Sync draft to backend (debounced)
   */
  const syncToBackend = useCallback(async (draftToSync: QuizDraft) => {
    // Use ref to avoid stale closure
    if (!isOnlineRef.current) return;

    try {
      setIsSyncing(true);
      setLastSyncError(null);

      await api.quiz.saveProgress({
        step: draftToSync.step,
        genres: draftToSync.selectedGenres,
        decades: draftToSync.selectedDecades,
        artist_affinities: Object.entries(draftToSync.artistAffinity).map(
          ([artist_name, affinity]) => ({ artist_name, affinity })
        ),
        manual_artists: draftToSync.manualArtists.map((a) => ({
          mbid: a.mbid,
          artist_id: a.spotify_id || a.artist_id,
          artist_name: a.name,
          genres: a.genres,
        })),
        enjoy_songs: draftToSync.enjoySongs,
        energy_preference: draftToSync.energyPreference,
        vocal_comfort_pref: draftToSync.vocalComfortPref,
        crowd_pleaser_pref: draftToSync.crowdPleaserPref,
      });

      pendingDraftRef.current = null;
    } catch (err) {
      console.warn("Failed to sync quiz draft to backend:", err);
      setLastSyncError(err instanceof Error ? err : new Error("Sync failed"));
      pendingDraftRef.current = draftToSync;
    } finally {
      setIsSyncing(false);
    }
  }, []);

  /**
   * Save draft to localStorage immediately and schedule backend sync
   */
  const saveDraft = useCallback((partialDraft: Partial<QuizDraft>) => {
    setDraft((prev) => {
      const newDraft: QuizDraft = {
        step: partialDraft.step ?? prev?.step ?? 1,
        selectedGenres: partialDraft.selectedGenres ?? prev?.selectedGenres ?? [],
        selectedDecades: partialDraft.selectedDecades ?? prev?.selectedDecades ?? [],
        artistAffinity: partialDraft.artistAffinity ?? prev?.artistAffinity ?? {},
        manualArtists: partialDraft.manualArtists ?? prev?.manualArtists ?? [],
        enjoySongs: partialDraft.enjoySongs ?? prev?.enjoySongs ?? [],
        energyPreference: partialDraft.energyPreference !== undefined
          ? partialDraft.energyPreference
          : prev?.energyPreference ?? null,
        vocalComfortPref: partialDraft.vocalComfortPref !== undefined
          ? partialDraft.vocalComfortPref
          : prev?.vocalComfortPref ?? null,
        crowdPleaserPref: partialDraft.crowdPleaserPref !== undefined
          ? partialDraft.crowdPleaserPref
          : prev?.crowdPleaserPref ?? null,
        lastSaved: Date.now(),
      };

      // Save to localStorage immediately
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(newDraft));
      } catch (err) {
        console.warn("Failed to save quiz draft to localStorage:", err);
      }

      // Schedule debounced backend sync
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
      pendingDraftRef.current = newDraft;
      debounceTimerRef.current = setTimeout(() => {
        syncToBackend(newDraft);
      }, DEBOUNCE_MS);

      return newDraft;
    });
  }, [syncToBackend]);

  /**
   * Clear draft from localStorage and state (call on successful submit)
   */
  const clearDraft = useCallback(() => {
    setDraft(null);
    pendingDraftRef.current = null;
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      console.warn("Failed to clear quiz draft from localStorage:", err);
    }
  }, []);

  return {
    draft,
    saveDraft,
    clearDraft,
    isSyncing,
    lastSyncError,
    isOnline,
  };
}
