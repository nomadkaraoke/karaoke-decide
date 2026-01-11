"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { SearchIcon, PlusIcon, CheckIcon } from "@/components/icons";
import { Input } from "@/components/ui";
import { api } from "@/lib/api";

export interface SearchableArtist {
  artist_id: string;
  artist_name: string;
  popularity: number;
  genres: string[];
}

export interface SelectedArtist {
  artist_id: string;
  artist_name: string;
  genres: string[];
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
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

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

  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setSearchResults([]);
      setIsOpen(false);
      return;
    }
    setIsSearching(true);
    try {
      const response = await api.catalog.searchArtists(query, 8);
      setSearchResults(response.artists);
      setIsOpen(true);
    } catch {
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, []);

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => handleSearch(value), 300);
  };

  const handleSelectArtist = (artist: SearchableArtist) => {
    const selectedArtist: SelectedArtist = {
      artist_id: artist.artist_id,
      artist_name: artist.artist_name,
      genres: artist.genres,
    };
    onSelect(selectedArtist);
    setSearchQuery("");
    setSearchResults([]);
    setIsOpen(false);
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
          {isSearching ? (
            <div className="w-4 h-4 border-2 border-[var(--brand-pink)] border-t-transparent rounded-full animate-spin" />
          ) : (
            <SearchIcon className="w-4 h-4 text-[var(--text-subtle)]" />
          )}
        </div>
        <Input
          type="text"
          placeholder={placeholder}
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
        <div className="absolute top-full left-0 right-0 mt-2 bg-[var(--card)] border border-[var(--card-border)] rounded-xl shadow-lg z-50 max-h-80 overflow-y-auto">
          {searchResults.length === 0 ? (
            <div className="p-4 text-center text-[var(--text-subtle)] text-sm">
              {isSearching
                ? "Searching..."
                : `No artists found for "${searchQuery}"`}
            </div>
          ) : (
            <div className="py-2">
              {searchResults.map((artist) => {
                const isSelected = selectedArtistIds.has(artist.artist_id);
                const genresDisplay = formatGenres(artist.genres);
                return (
                  <button
                    key={artist.artist_id}
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
                        {artist.artist_name}
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
            </div>
          )}
        </div>
      )}
    </div>
  );
}
