"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { SearchIcon, PlusIcon, CheckIcon } from "@/components/icons";
import { Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useArtistIndex } from "@/hooks/useArtistIndex";

/**
 * Artist from search results.
 * MBID-first: MusicBrainz ID is the primary identifier when available.
 */
export interface SearchableArtist {
  // Primary identifier (MusicBrainz)
  mbid: string | null;
  name: string;

  // Spotify enrichment (optional)
  spotify_id: string | null;
  popularity: number;
  genres: string[];

  // Backward compatibility (deprecated)
  artist_id?: string; // Use mbid or spotify_id instead
  artist_name?: string; // Use name instead
}

/**
 * Selected artist for submission.
 * MBID-first: MusicBrainz ID is the primary identifier when available.
 */
export interface SelectedArtist {
  mbid: string | null; // Primary identifier
  spotify_id: string | null; // For images, backward compat
  name: string;
  genres: string[];

  // Backward compatibility (deprecated)
  artist_id?: string; // Use mbid or spotify_id instead
  artist_name?: string; // Use name instead
}

interface ArtistSearchAutocompleteProps {
  onSelect: (artist: SelectedArtist) => void;
  selectedArtistIds?: Set<string>;
  placeholder?: string;
  className?: string;
}

export function ArtistSearchAutocomplete({
  onSelect,
  selectedArtistIds = new Set(),
  placeholder = "Search artists to add...",
  className = "",
}: ArtistSearchAutocompleteProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchableArtist[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [showApiSearch, setShowApiSearch] = useState(false);
  const [isApiSearching, setIsApiSearching] = useState(false);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Client-side search index
  const { isReady: indexReady, search: searchIndex, isLoading: indexLoading } = useArtistIndex();

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Client-side search (instant)
  // MBID-first: Map local index results to new format
  const handleLocalSearch = useCallback(
    (query: string) => {
      if (!query.trim() || !indexReady) {
        setSearchResults([]);
        setShowApiSearch(false);
        return;
      }

      const results = searchIndex(query, 8);
      const mapped: SearchableArtist[] = results.map((r) => ({
        // MBID-first: Use mbid as primary, spotify_id for images
        mbid: r.mbid,
        name: r.name,
        spotify_id: r.spotify_id,
        popularity: r.popularity,
        genres: [], // Local index doesn't include genres
        // Backward compat (deprecated)
        artist_id: r.spotify_id || r.mbid || "",
        artist_name: r.name,
      }));

      setSearchResults(mapped);
      setShowApiSearch(true); // Always show option to search more
      setIsOpen(true);
    },
    [indexReady, searchIndex]
  );

  // Helper to get unique ID for an artist (MBID-first)
  const getArtistUniqueId = (artist: SearchableArtist): string => {
    return artist.mbid || artist.spotify_id || artist.name;
  };

  // API search (for niche artists not in local index)
  const handleApiSearch = useCallback(async (query: string) => {
    if (!query.trim()) return;

    setIsApiSearching(true);
    try {
      const response = await api.catalog.searchArtists(query, 15);
      // Merge with existing results, deduplicate by MBID-first unique ID
      setSearchResults((prev) => {
        const existingIds = new Set(prev.map((a) => getArtistUniqueId(a)));
        const newArtists = response.artists.filter(
          (a: SearchableArtist) => !existingIds.has(getArtistUniqueId(a))
        );
        return [...prev, ...newArtists];
      });
      setShowApiSearch(false); // Hide "search more" after API search
    } catch (err) {
      console.error("API search failed:", err);
    } finally {
      setIsApiSearching(false);
    }
  }, []);

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    setShowApiSearch(false);

    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);

    if (!value.trim()) {
      setSearchResults([]);
      setIsOpen(false);
      return;
    }

    // If index is ready, search locally (instant)
    if (indexReady) {
      handleLocalSearch(value);
    } else {
      // Fallback to API search with debounce if index not ready
      setIsSearching(true);
      searchTimeoutRef.current = setTimeout(async () => {
        try {
          const response = await api.catalog.searchArtists(value, 8);
          setSearchResults(response.artists);
          setIsOpen(true);
        } catch {
          setSearchResults([]);
        } finally {
          setIsSearching(false);
        }
      }, 150);
    }
  };

  const handleSelectArtist = (artist: SearchableArtist) => {
    // MBID-first: Pass both mbid (primary) and spotify_id (for images)
    const selectedArtist: SelectedArtist = {
      mbid: artist.mbid,
      spotify_id: artist.spotify_id,
      name: artist.name,
      genres: artist.genres,
      // Backward compat (deprecated)
      artist_id: artist.spotify_id || artist.mbid || "",
      artist_name: artist.name,
    };
    onSelect(selectedArtist);
    setSearchQuery("");
    setSearchResults([]);
    setIsOpen(false);
    setShowApiSearch(false);
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, []);

  // Format genres for display (show first 2)
  const formatGenres = (genres: string[]) => {
    if (!genres || genres.length === 0) return null;
    const display = genres.slice(0, 2).join(", ");
    return genres.length > 2 ? `${display}, +${genres.length - 2}` : display;
  };

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      {/* Search Input */}
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
          {isSearching || isApiSearching ? (
            <div className="w-4 h-4 border-2 border-[var(--brand-pink)] border-t-transparent rounded-full animate-spin" />
          ) : (
            <SearchIcon className="w-4 h-4 text-[var(--text-subtle)]" />
          )}
        </div>
        <Input
          type="text"
          placeholder={indexLoading ? "Loading artists..." : placeholder}
          value={searchQuery}
          onChange={(e) => handleSearchChange(e.target.value)}
          onFocus={() => {
            if (searchResults.length > 0) setIsOpen(true);
          }}
          className="pl-10"
        />
      </div>

      {/* Search Results Dropdown */}
      {isOpen && searchQuery.trim() && (
        <div className="absolute top-full left-0 right-0 mt-2 bg-[var(--card)] border border-[var(--card-border)] rounded-xl shadow-lg z-50 max-h-96 overflow-y-auto">
          {searchResults.length === 0 && !isSearching && !isApiSearching ? (
            <div className="p-4">
              <p className="text-center text-[var(--text-subtle)] text-sm mb-3">
                No artists found for &quot;{searchQuery}&quot;
              </p>
              {indexReady && (
                <button
                  onClick={() => handleApiSearch(searchQuery)}
                  className="w-full py-2 px-4 text-sm text-[var(--brand-pink)] hover:bg-[var(--secondary)] rounded-lg transition-colors"
                >
                  Search full database...
                </button>
              )}
            </div>
          ) : (
            <div className="py-2">
              {searchResults.map((artist) => {
                // MBID-first: Use unique ID for key and selection check
                const uniqueId = getArtistUniqueId(artist);
                const isSelected = selectedArtistIds.has(uniqueId);
                const genresDisplay = formatGenres(artist.genres);
                return (
                  <button
                    key={uniqueId}
                    onClick={() => handleSelectArtist(artist)}
                    disabled={isSelected}
                    className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors ${
                      isSelected
                        ? "bg-green-500/10 cursor-default"
                        : "hover:bg-[var(--secondary)]"
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[var(--text)] font-medium truncate">
                        {artist.name}
                      </div>
                      {genresDisplay && (
                        <div className="text-[var(--text-muted)] text-sm truncate">
                          {genresDisplay}
                        </div>
                      )}
                    </div>
                    {isSelected ? (
                      <span className="flex items-center gap-1 text-green-400 text-sm">
                        <CheckIcon className="w-4 h-4" />
                        Added
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[var(--brand-pink)] text-sm">
                        <PlusIcon className="w-4 h-4" />
                        Add
                      </span>
                    )}
                  </button>
                );
              })}

              {/* Search more button */}
              {showApiSearch && searchResults.length > 0 && (
                <button
                  onClick={() => handleApiSearch(searchQuery)}
                  disabled={isApiSearching}
                  className="w-full py-3 px-4 text-sm text-[var(--text-muted)] hover:text-[var(--brand-pink)] hover:bg-[var(--secondary)] border-t border-[var(--card-border)] transition-colors flex items-center justify-center gap-2"
                >
                  {isApiSearching ? (
                    <>
                      <div className="w-3 h-3 border-2 border-[var(--brand-pink)] border-t-transparent rounded-full animate-spin" />
                      Searching...
                    </>
                  ) : (
                    <>
                      <SearchIcon className="w-3 h-3" />
                      Search for more artists...
                    </>
                  )}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
