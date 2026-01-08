"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import {
  MusicIcon,
  ChevronDownIcon,
  SpotifyIcon,
  XIcon,
  PlusIcon,
} from "@/components/icons";
import { Button, Input, SourceBadge, LoadingPulse } from "@/components/ui";

interface UserSong {
  id: string;
  song_id: string;
  artist: string;
  title: string;
  source: string;
  sync_count?: number;
  playcount?: number;
  rank?: number;
  is_saved: boolean;
  times_sung: number;
  spotify_popularity?: number;
  spotify_track_id?: string;
  has_karaoke_version?: boolean;
}

interface TrackSuggestion {
  track_id: string;
  track_name: string;
  artist_name: string;
  artist_id: string;
  popularity: number;
  duration_ms: number;
  explicit: boolean;
}

interface Props {
  isExpanded: boolean;
  onToggle: () => void;
  refreshTrigger?: number;
}

export function YourSongsSection({
  isExpanded,
  onToggle,
  refreshTrigger,
}: Props) {
  const [songs, setSongs] = useState<UserSong[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Add song form
  const [searchQuery, setSearchQuery] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Autocomplete state
  const [suggestions, setSuggestions] = useState<TrackSuggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedSuggestion, setSelectedSuggestion] =
    useState<TrackSuggestion | null>(null);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Remove song state
  const [removingSong, setRemovingSong] = useState<string | null>(null);

  const loadSongs = useCallback(
    async (pageNum: number, append: boolean = false) => {
      try {
        if (append) {
          setIsLoadingMore(true);
        } else {
          setIsLoading(true);
        }
        setError(null);

        const response = await api.my.getSongs(pageNum, 20);

        if (append) {
          setSongs((prev) => [...prev, ...(response.songs as UserSong[])]);
        } else {
          setSongs(response.songs as UserSong[]);
        }
        setTotal(response.total);
        setHasMore(response.has_more);
        setPage(pageNum);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load your songs"
        );
      } finally {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    },
    []
  );

  useEffect(() => {
    loadSongs(1);
  }, [loadSongs, refreshTrigger]);

  // Search for track suggestions
  const searchTracks = useCallback(async (query: string) => {
    if (query.length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    try {
      const response = await api.catalog.searchTracks(query, 8);
      setSuggestions(response.tracks);
      setShowSuggestions(response.tracks.length > 0);
      setHighlightedIndex(-1);
    } catch (err) {
      console.error("Track search failed:", err);
      setSuggestions([]);
    }
  }, []);

  // Handle input change with debounced search
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setSearchQuery(value);
    setSelectedSuggestion(null);

    // Debounce search
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }
    searchTimeoutRef.current = setTimeout(() => {
      searchTracks(value);
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
  const selectSuggestion = (suggestion: TrackSuggestion) => {
    setSelectedSuggestion(suggestion);
    setSearchQuery(`${suggestion.track_name} - ${suggestion.artist_name}`);
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

  const handleAddSong = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedSuggestion) return;

    try {
      setIsAdding(true);
      setAddError(null);
      await api.knownSongs.addSpotifyTrack(selectedSuggestion.track_id);
      setSearchQuery("");
      setSelectedSuggestion(null);
      setSuggestions([]);
      await loadSongs(1);
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to add song");
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemoveSong = async (song: UserSong) => {
    try {
      setRemovingSong(song.id);
      // Check if it's a Spotify track or karaoke catalog song
      if (song.spotify_track_id) {
        await api.knownSongs.removeSpotifyTrack(song.spotify_track_id);
      } else if (song.song_id?.startsWith("spotify:")) {
        // Handle spotify-prefixed song_id (e.g., "spotify:trackId")
        const trackId = song.song_id.split(":").pop();
        if (trackId) {
          await api.knownSongs.removeSpotifyTrack(trackId);
        }
      } else if (song.song_id) {
        // Karaoke catalog song with numeric ID
        await api.knownSongs.remove(parseInt(song.song_id, 10));
      }
      await loadSongs(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove song");
    } finally {
      setRemovingSong(null);
    }
  };

  const handleLoadMore = () => {
    if (!isLoadingMore && hasMore) {
      loadSongs(page + 1, true);
    }
  };

  const formatDuration = (ms: number) => {
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.floor((ms % 60000) / 1000);
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
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
          <div className="w-10 h-10 rounded-full bg-[var(--brand-purple)]/20 flex items-center justify-center">
            <MusicIcon className="w-5 h-5 text-[var(--brand-purple)]" />
          </div>
          <div>
            <h2 className="font-semibold text-[var(--text)]">Songs You Know</h2>
            <p className="text-sm text-[var(--text-muted)]">
              {total === 0
                ? "No songs yet"
                : `${total} song${total !== 1 ? "s" : ""} in your library`}
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
            <LoadingPulse count={5} />
          ) : (
            <>
              {/* Add song form with autocomplete */}
              <form onSubmit={handleAddSong} className="flex gap-2">
                <div className="flex-1 relative">
                  <Input
                    ref={inputRef}
                    placeholder="Search for a song..."
                    value={searchQuery}
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
                          key={suggestion.track_id}
                          type="button"
                          onClick={() => selectSuggestion(suggestion)}
                          className={`w-full px-3 py-2 text-left transition-colors ${
                            index === highlightedIndex
                              ? "bg-[var(--brand-purple)]/20"
                              : "hover:bg-[var(--secondary)]"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex-1 min-w-0">
                              <span className="text-sm font-medium text-[var(--text)] truncate block">
                                {suggestion.track_name}
                              </span>
                              <span className="text-xs text-[var(--text-muted)] truncate block">
                                {suggestion.artist_name}
                              </span>
                            </div>
                            <div className="flex items-center gap-2 ml-2">
                              <span className="text-xs text-[var(--text-subtle)]">
                                {formatDuration(suggestion.duration_ms)}
                              </span>
                              {suggestion.explicit && (
                                <span className="text-xs px-1 py-0.5 rounded bg-[var(--secondary)] text-[var(--text-muted)]">
                                  E
                                </span>
                              )}
                            </div>
                          </div>
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
                  disabled={!selectedSuggestion}
                  leftIcon={<PlusIcon className="w-4 h-4" />}
                >
                  Add
                </Button>
              </form>

              {/* Song list */}
              {total === 0 ? (
                <div className="text-center py-8 text-[var(--text-subtle)] text-sm">
                  <p>No songs in your library yet.</p>
                  <p className="mt-1">
                    Search for songs above, connect your music services, or take
                    the quiz.
                  </p>
                </div>
              ) : (
                <>
                  <div className="space-y-2">
                    {songs.map((song, index) => (
                      <div
                        key={song.id}
                        className="group flex items-center gap-3 p-3 rounded-xl bg-[var(--secondary)] hover:bg-[var(--secondary)]/80 transition-colors"
                      >
                        <span className="text-xs text-[var(--text-subtle)] w-6 text-right">
                          {index + 1}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium text-[var(--text)] truncate">
                              {song.title}
                            </p>
                            {song.has_karaoke_version === false && (
                              <span className="text-xs px-1.5 py-0.5 rounded bg-[#1DB954]/20 text-[#1DB954]/80 whitespace-nowrap">
                                Make Karaoke
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-[var(--text-muted)] truncate">
                            {song.artist}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          {song.playcount && song.playcount > 0 && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--secondary)] text-[var(--text-subtle)]">
                              {song.playcount.toLocaleString()} plays
                            </span>
                          )}
                          {song.rank && song.rank <= 50 && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--brand-purple)]/20 text-[var(--brand-purple)]">
                              #{song.rank}
                            </span>
                          )}
                          <SourceBadge
                            source={song.source as "spotify" | "lastfm" | "quiz"}
                          />
                          {song.source === "known_songs" && (
                            <button
                              onClick={() => handleRemoveSong(song)}
                              disabled={removingSong === song.id}
                              className="opacity-0 group-hover:opacity-100 p-1 text-[var(--text-subtle)] hover:text-[var(--error)] transition-all"
                              title="Remove song"
                            >
                              {removingSong === song.id ? (
                                <span className="animate-pulse">...</span>
                              ) : (
                                <XIcon className="w-3.5 h-3.5" />
                              )}
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Load more */}
                  {hasMore && (
                    <div className="text-center">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={handleLoadMore}
                        isLoading={isLoadingMore}
                      >
                        Load More
                      </Button>
                    </div>
                  )}

                  {/* Summary */}
                  <div className="text-center text-xs text-[var(--text-subtle)]">
                    Showing {songs.length} of {total} songs
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
