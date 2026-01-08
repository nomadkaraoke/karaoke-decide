"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import {
  SpotifyIcon,
  LastfmIcon,
  ChevronDownIcon,
  XIcon,
  PlusIcon,
} from "@/components/icons";
import { Button, Input, LoadingPulse } from "@/components/ui";

interface UserArtist {
  artist_name: string;
  sources: string[];
  spotify_rank: number | null;
  spotify_time_range: string | null;
  lastfm_rank: number | null;
  lastfm_playcount: number | null;
  popularity: number | null;
  genres: string[];
  is_excluded: boolean;
  is_manual: boolean;
}

interface ArtistSuggestion {
  artist_id: string;
  artist_name: string;
  popularity: number;
  genres: string[];
}

interface Props {
  isExpanded: boolean;
  onToggle: () => void;
  refreshTrigger?: number;
}

export function YourArtistsSection({
  isExpanded,
  onToggle,
  refreshTrigger,
}: Props) {
  const [artists, setArtists] = useState<UserArtist[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Add artist form
  const [newArtist, setNewArtist] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Autocomplete state
  const [suggestions, setSuggestions] = useState<ArtistSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedSuggestion, setSelectedSuggestion] =
    useState<ArtistSuggestion | null>(null);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Remove artist state
  const [removingArtist, setRemovingArtist] = useState<string | null>(null);

  const loadArtists = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.my.getDataArtists();
      setArtists(response.artists);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load artists");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadArtists();
  }, [loadArtists, refreshTrigger]);

  // Search for artist suggestions
  const searchArtists = useCallback(async (query: string) => {
    if (query.length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    try {
      const response = await api.catalog.searchArtists(query, 8);
      setSuggestions(response.artists);
      setShowSuggestions(response.artists.length > 0);
      setHighlightedIndex(-1);
    } catch (err) {
      console.error("Artist search failed:", err);
      setSuggestions([]);
    }
  }, []);

  // Handle input change with debounced search
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setNewArtist(value);
    setSelectedSuggestion(null);

    // Debounce search
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }
    searchTimeoutRef.current = setTimeout(() => {
      searchArtists(value);
    }, 200);
  };

  // Cleanup debounced search on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
      }
    };
  }, []);

  // Handle suggestion selection
  const selectSuggestion = (suggestion: ArtistSuggestion) => {
    setSelectedSuggestion(suggestion);
    setNewArtist(suggestion.artist_name);
    setShowSuggestions(false);
    setSuggestions([]);
    inputRef.current?.focus();
  };

  // Handle keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!showSuggestions || suggestions.length === 0) return;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setHighlightedIndex((prev) =>
          prev < suggestions.length - 1 ? prev + 1 : prev
        );
        break;
      case "ArrowUp":
        e.preventDefault();
        setHighlightedIndex((prev) => (prev > 0 ? prev - 1 : -1));
        break;
      case "Enter":
        e.preventDefault();
        if (highlightedIndex >= 0) {
          selectSuggestion(suggestions[highlightedIndex]);
        }
        break;
      case "Escape":
        setShowSuggestions(false);
        break;
    }
  };

  // Close suggestions when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        suggestionsRef.current &&
        !suggestionsRef.current.contains(e.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(e.target as Node)
      ) {
        setShowSuggestions(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleAddArtist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newArtist.trim()) return;

    try {
      setIsAdding(true);
      setAddError(null);
      // Pass spotify_artist_id if we selected from autocomplete
      await api.my.addDataArtist(
        newArtist.trim(),
        selectedSuggestion?.artist_id
      );
      setNewArtist("");
      setSelectedSuggestion(null);
      setSuggestions([]);
      await loadArtists();
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to add artist");
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemoveArtist = async (artistName: string) => {
    try {
      setRemovingArtist(artistName);
      await api.my.removeDataArtist(artistName);
      await loadArtists();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to remove artist"
      );
    } finally {
      setRemovingArtist(null);
    }
  };

  const sourceConfig: Record<
    string,
    { icon: React.ReactNode; color: string; bg: string }
  > = {
    spotify: {
      icon: <SpotifyIcon className="w-3 h-3" />,
      color: "#1DB954",
      bg: "bg-[#1DB954]/20",
    },
    lastfm: {
      icon: <LastfmIcon className="w-3 h-3" />,
      color: "#ff4444",
      bg: "bg-[#ff4444]/20",
    },
    quiz: {
      icon: <span className="text-xs">✓</span>,
      color: "var(--brand-pink)",
      bg: "bg-[var(--brand-pink)]/20",
    },
  };

  const formatPlaycount = (count: number) => {
    if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
    return count.toString();
  };

  return (
    <div className="rounded-2xl bg-[var(--card)] border border-[var(--card-border)] overflow-hidden">
      {/* Header */}
      <button
        onClick={onToggle}
        aria-expanded={isExpanded}
        className="w-full p-5 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[var(--brand-pink)]/20 flex items-center justify-center">
            <span className="text-lg text-[var(--brand-pink)]">★</span>
          </div>
          <div>
            <h2 className="font-semibold text-[var(--text)]">Artists You Know</h2>
            <p className="text-sm text-[var(--text-muted)]">
              {artists.length === 0
                ? "No artists yet"
                : `${artists.length} artist${artists.length !== 1 ? "s" : ""}`}
            </p>
          </div>
        </div>
        <ChevronDownIcon
          className={`w-5 h-5 text-[var(--text-muted)] transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* Content */}
      {isExpanded && (
        <div className="px-5 pb-5 space-y-4">
          {/* Error message */}
          {error && (
            <div className="p-3 rounded-xl bg-[var(--error)]/10 border border-[var(--error)]/30 text-[var(--error)] text-sm">
              {error}
              <button
                onClick={() => setError(null)}
                className="ml-2 underline hover:no-underline"
              >
                Dismiss
              </button>
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={3} />
          ) : (
            <>
              {/* Add artist form with autocomplete */}
              <form onSubmit={handleAddArtist} className="flex gap-2">
                <div className="flex-1 relative">
                  <Input
                    ref={inputRef}
                    placeholder="Search for an artist..."
                    value={newArtist}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    onFocus={() => {
                      if (suggestions.length > 0) setShowSuggestions(true);
                    }}
                    error={addError || undefined}
                    autoComplete="off"
                  />
                  {/* Autocomplete dropdown */}
                  {showSuggestions && suggestions.length > 0 && (
                    <div
                      ref={suggestionsRef}
                      className="absolute z-50 top-full left-0 right-0 mt-1 bg-[var(--card)] border border-[var(--card-border)] rounded-xl shadow-xl overflow-hidden"
                    >
                      {suggestions.map((suggestion, index) => (
                        <button
                          key={suggestion.artist_id}
                          type="button"
                          onClick={() => selectSuggestion(suggestion)}
                          className={`w-full px-3 py-2 text-left transition-colors ${
                            index === highlightedIndex
                              ? "bg-[var(--brand-pink)]/20"
                              : "hover:bg-[var(--secondary)]"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-sm font-medium text-[var(--text)]">
                              {suggestion.artist_name}
                            </span>
                            {suggestion.popularity > 0 && (
                              <span className="text-xs text-[var(--text-subtle)]">
                                {suggestion.popularity}%
                              </span>
                            )}
                          </div>
                          {suggestion.genres.length > 0 && (
                            <div className="text-xs text-[var(--text-muted)] mt-0.5 capitalize">
                              {suggestion.genres.slice(0, 2).join(", ")}
                            </div>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                  {/* Selected indicator */}
                  {selectedSuggestion && (
                    <div className="absolute right-2 top-1/2 -translate-y-1/2">
                      <SpotifyIcon className="w-4 h-4 text-[#1DB954]" />
                    </div>
                  )}
                </div>
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  isLoading={isAdding}
                  disabled={!newArtist.trim()}
                  leftIcon={<PlusIcon className="w-4 h-4" />}
                >
                  Add
                </Button>
              </form>

              {/* Artist list */}
              {artists.length === 0 ? (
                <div className="text-center py-8 text-[var(--text-subtle)] text-sm">
                  <p>No artists yet.</p>
                  <p className="mt-1">
                    Add artists manually, take the quiz, or sync your music
                    services.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {artists.slice(0, 30).map((artist) => (
                    <div
                      key={artist.artist_name}
                      className={`group flex items-center gap-2 px-3 py-2 rounded-xl transition-colors ${
                        artist.is_excluded
                          ? "bg-[var(--secondary)]/50 opacity-60"
                          : "bg-[var(--secondary)] hover:bg-[var(--secondary)]/80"
                      }`}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-[var(--text)] truncate">
                            {artist.artist_name}
                          </span>
                          {/* Source badges */}
                          {artist.sources.map((source) => {
                            const config = sourceConfig[source] || {
                              icon: null,
                              color: "#999",
                              bg: "bg-[var(--secondary)]",
                            };
                            return (
                              <span
                                key={source}
                                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs ${config.bg}`}
                                style={{ color: config.color }}
                              >
                                {config.icon}
                              </span>
                            );
                          })}
                          {artist.is_excluded && (
                            <span className="text-xs text-[var(--warning)] bg-[var(--warning)]/10 px-1.5 py-0.5 rounded">
                              Hidden
                            </span>
                          )}
                        </div>
                        {/* Stats line */}
                        <div className="flex items-center gap-2 mt-0.5 text-xs text-[var(--text-subtle)]">
                          {artist.spotify_rank && (
                            <span>#{artist.spotify_rank} Spotify</span>
                          )}
                          {artist.lastfm_playcount && artist.lastfm_playcount > 0 && (
                            <span>{formatPlaycount(artist.lastfm_playcount)} plays</span>
                          )}
                          {artist.genres.length > 0 && (
                            <span className="capitalize">{artist.genres[0]}</span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() => handleRemoveArtist(artist.artist_name)}
                        disabled={removingArtist === artist.artist_name}
                        className="opacity-0 group-hover:opacity-100 p-1 text-[var(--text-subtle)] hover:text-[var(--error)] transition-all"
                        title="Remove artist"
                      >
                        {removingArtist === artist.artist_name ? (
                          <span className="animate-pulse">...</span>
                        ) : (
                          <XIcon className="w-3.5 h-3.5" />
                        )}
                      </button>
                    </div>
                  ))}
                  {artists.length > 30 && (
                    <div className="text-center py-2 text-sm text-[var(--text-subtle)]">
                      +{artists.length - 30} more artists
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
