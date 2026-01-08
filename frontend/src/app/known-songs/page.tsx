"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { ProtectedPage } from "@/components/ProtectedPage";
import {
  MicrophoneIcon,
  SearchIcon,
  PlusIcon,
  CheckIcon,
  TrashIcon,
  SparklesIcon,
} from "@/components/icons";
import { PopularityStars } from "@/components/SongCard";
import { Button, LoadingPulse, EmptyState, Input } from "@/components/ui";

interface CatalogSong {
  id: number;
  artist: string;
  title: string;
  brands: string[];
  brand_count: number;
  is_popular: boolean;
}

interface KnownSong {
  id: string;
  song_id: string;
  artist: string;
  title: string;
  source: string;
  is_saved: boolean;
  created_at: string;
  updated_at: string;
}

export default function KnownSongsPage() {
  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<CatalogSong[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [addedSongIds, setAddedSongIds] = useState<Set<number>>(new Set());
  const [addingIds, setAddingIds] = useState<Set<number>>(new Set());
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Known songs list state
  const [knownSongs, setKnownSongs] = useState<KnownSong[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [removingIds, setRemovingIds] = useState<Set<string>>(new Set());

  // Load user's known songs
  const loadKnownSongs = useCallback(
    async (pageNum: number, append: boolean = false) => {
      try {
        if (append) {
          setIsLoadingMore(true);
        } else {
          setIsLoading(true);
        }
        setListError(null);

        const response = await api.knownSongs.list(pageNum, 20);

        if (append) {
          setKnownSongs((prev) => [...prev, ...response.songs]);
        } else {
          setKnownSongs(response.songs);
        }
        setTotal(response.total);
        setHasMore(pageNum * response.per_page < response.total);
        setPage(pageNum);

        // Update added song IDs set
        const songIds = new Set(response.songs.map((s) => parseInt(s.song_id)));
        if (append) {
          setAddedSongIds((prev) => new Set([...prev, ...songIds]));
        } else {
          setAddedSongIds(songIds);
        }
      } catch (err) {
        setListError(
          err instanceof Error ? err.message : "Failed to load known songs"
        );
      } finally {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    },
    []
  );

  useEffect(() => {
    loadKnownSongs(1);
  }, [loadKnownSongs]);

  // Cleanup debounce timeout on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = null;
      }
    };
  }, []);

  // Search for songs in catalog
  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setSearchResults([]);
      setSearchError(null);
      return;
    }

    setIsSearching(true);
    setSearchError(null);

    try {
      const response = await api.catalog.searchSongs(query, 10);
      setSearchResults(response.songs);
    } catch (err) {
      setSearchError(
        err instanceof Error ? err.message : "Search failed"
      );
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, []);

  // Debounced search
  const handleSearchChange = (value: string) => {
    setSearchQuery(value);

    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }

    searchTimeoutRef.current = setTimeout(() => {
      handleSearch(value);
    }, 300);
  };

  // Add song to known songs
  const handleAddSong = async (song: CatalogSong) => {
    if (addedSongIds.has(song.id) || addingIds.has(song.id)) {
      return;
    }

    setAddingIds((prev) => new Set([...prev, song.id]));

    try {
      const result = await api.knownSongs.add(song.id);

      if (result.added || result.already_existed) {
        setAddedSongIds((prev) => new Set([...prev, song.id]));
        // Reload the list to show the new song
        if (result.added) {
          loadKnownSongs(1);
        }
      }
    } catch (err) {
      // Show error briefly
      console.error("Failed to add song:", err);
    } finally {
      setAddingIds((prev) => {
        const next = new Set(prev);
        next.delete(song.id);
        return next;
      });
    }
  };

  // Remove song from known songs
  const handleRemoveSong = async (song: KnownSong) => {
    const songId = parseInt(song.song_id);
    setRemovingIds((prev) => new Set([...prev, song.id]));

    try {
      await api.knownSongs.remove(songId);

      // Remove from local state
      setKnownSongs((prev) => prev.filter((s) => s.id !== song.id));
      setTotal((prev) => prev - 1);
      setAddedSongIds((prev) => {
        const next = new Set(prev);
        next.delete(songId);
        return next;
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // Song was already removed, update local state
        setKnownSongs((prev) => prev.filter((s) => s.id !== song.id));
        setTotal((prev) => prev - 1);
      } else {
        console.error("Failed to remove song:", err);
      }
    } finally {
      setRemovingIds((prev) => {
        const next = new Set(prev);
        next.delete(song.id);
        return next;
      });
    }
  };

  const handleLoadMore = () => {
    if (!isLoadingMore && hasMore) {
      loadKnownSongs(page + 1, true);
    }
  };

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
                <MicrophoneIcon className="w-7 h-7 text-[var(--brand-pink)]" />
                Songs I Know
              </h1>
              <p className="text-[var(--text-muted)] text-sm mt-1">
                Add songs you already love singing to improve recommendations
              </p>
            </div>
          </div>

          {/* Search Section */}
          <div className="mb-8">
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                <SearchIcon className="w-5 h-5 text-[var(--text-subtle)]" />
              </div>
              <Input
                type="text"
                placeholder="Search for songs to add..."
                value={searchQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
                className="pl-12"
                data-testid="song-search-input"
              />
            </div>

            {/* Search Results */}
            {searchQuery.trim() && (
              <div className="mt-4">
                {isSearching ? (
                  <div className="flex items-center justify-center py-8">
                    <div className="animate-spin w-6 h-6 border-2 border-[var(--brand-blue)] border-t-transparent rounded-full" />
                  </div>
                ) : searchError ? (
                  <p className="text-red-400 text-sm text-center py-4">
                    {searchError}
                  </p>
                ) : searchResults.length === 0 ? (
                  <p className="text-[var(--text-subtle)] text-sm text-center py-4">
                    No songs found for &quot;{searchQuery}&quot;
                  </p>
                ) : (
                  <div className="flex flex-col gap-2">
                    <p className="text-[var(--text-muted)] text-sm mb-2">
                      Search results ({searchResults.length})
                    </p>
                    {searchResults.map((song) => {
                      const isAdded = addedSongIds.has(song.id);
                      const isAdding = addingIds.has(song.id);

                      return (
                        <div
                          key={song.id}
                          className="flex items-center gap-3 p-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)]"
                          data-testid="search-result-item"
                        >
                          <div className="flex-1 min-w-0">
                            <h3 className="text-[var(--text)] font-medium truncate">
                              {song.title}
                            </h3>
                            <p className="text-[var(--text-muted)] text-sm truncate">
                              {song.artist}
                            </p>
                          </div>

                          <PopularityStars count={song.brand_count} />

                          <button
                            onClick={() => handleAddSong(song)}
                            disabled={isAdded || isAdding}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
                              isAdded
                                ? "bg-green-500/20 text-green-400 cursor-default"
                                : isAdding
                                ? "bg-[var(--secondary)] text-[var(--text-subtle)] cursor-wait"
                                : "bg-[var(--brand-pink)]/20 text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/30"
                            }`}
                            data-testid="add-song-button"
                          >
                            {isAdded ? (
                              <>
                                <CheckIcon className="w-4 h-4" />
                                Added
                              </>
                            ) : isAdding ? (
                              <>
                                <div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
                                Adding
                              </>
                            ) : (
                              <>
                                <PlusIcon className="w-4 h-4" />
                                Add
                              </>
                            )}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="border-t border-[var(--card-border)] mb-6" />

          {/* Known Songs List */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-[var(--text)]">
                My Known Songs
              </h2>
              {total > 0 && (
                <span className="text-[var(--text-subtle)] text-sm">{total} songs</span>
              )}
            </div>

            {isLoading ? (
              <LoadingPulse count={5} />
            ) : listError ? (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mb-3">
                  <span className="text-xl">!</span>
                </div>
                <p className="text-[var(--text-muted)] mb-4">{listError}</p>
                <Button onClick={() => loadKnownSongs(1)} variant="secondary">
                  Try again
                </Button>
              </div>
            ) : knownSongs.length === 0 ? (
              <EmptyState
                icon={<MicrophoneIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
                title="No known songs yet"
                description="Search above to add songs you know and love to sing!"
                action={{
                  label: "View Recommendations",
                  onClick: () => (window.location.href = "/recommendations"),
                }}
              />
            ) : (
              <>
                <div className="flex flex-col gap-2">
                  {knownSongs.map((song) => {
                    const isRemoving = removingIds.has(song.id);

                    return (
                      <div
                        key={song.id}
                        className={`flex items-center gap-3 p-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)] transition-opacity ${
                          isRemoving ? "opacity-50" : ""
                        }`}
                        data-testid="known-song-item"
                      >
                        <div className="flex-1 min-w-0">
                          <h3 className="text-[var(--text)] font-medium truncate">
                            {song.title}
                          </h3>
                          <p className="text-[var(--text-muted)] text-sm truncate">
                            {song.artist}
                          </p>
                        </div>

                        <button
                          onClick={() => handleRemoveSong(song)}
                          disabled={isRemoving}
                          className="p-2 rounded-full text-[var(--text-subtle)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                          title="Remove from known songs"
                          data-testid="remove-song-button"
                        >
                          {isRemoving ? (
                            <div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
                          ) : (
                            <TrashIcon className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>

                {/* Load more */}
                {hasMore && (
                  <div className="mt-6 text-center">
                    <Button
                      variant="secondary"
                      onClick={handleLoadMore}
                      isLoading={isLoadingMore}
                    >
                      Load More
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Footer */}
          <div className="mt-8 pt-6 border-t border-[var(--card-border)]">
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
              <Link
                href="/recommendations"
                className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
              >
                <SparklesIcon className="w-4 h-4" />
                View recommendations based on your songs
              </Link>
            </div>
          </div>
        </div>
      </main>
    </ProtectedPage>
  );
}
