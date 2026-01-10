"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { SearchIcon, PlusIcon, CheckIcon } from "@/components/icons";
import { Input } from "@/components/ui";
import { api } from "@/lib/api";
import { PopularityStars } from "@/components/SongCard";

export interface SearchableSong {
  id: number;
  artist: string;
  title: string;
  brands: string[];
  brand_count: number;
  is_popular: boolean;
}

export interface SelectedSong {
  song_id: string;
  artist: string;
  title: string;
}

interface SongSearchAutocompleteProps {
  onSelect: (song: SelectedSong) => void;
  selectedSongIds?: Set<string>;
  placeholder?: string;
  className?: string;
}

export function SongSearchAutocomplete({
  onSelect,
  selectedSongIds = new Set(),
  placeholder = "Search songs to add...",
  className = "",
}: SongSearchAutocompleteProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchableSong[]>([]);
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
      const response = await api.catalog.searchSongs(query, 8);
      setSearchResults(response.songs);
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

  const handleSelectSong = (song: SearchableSong) => {
    const selectedSong: SelectedSong = {
      song_id: song.id.toString(),
      artist: song.artist,
      title: song.title,
    };
    onSelect(selectedSong);
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
                : `No songs found for "${searchQuery}"`}
            </div>
          ) : (
            <div className="py-2">
              {searchResults.map((song) => {
                const isSelected = selectedSongIds.has(song.id.toString());
                return (
                  <button
                    key={song.id}
                    onClick={() => handleSelectSong(song)}
                    disabled={isSelected}
                    className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors ${
                      isSelected
                        ? "bg-green-500/10 cursor-default"
                        : "hover:bg-[var(--secondary)]"
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[var(--text)] font-medium truncate">
                        {song.title}
                      </div>
                      <div className="text-[var(--text-muted)] text-sm truncate">
                        {song.artist}
                      </div>
                    </div>
                    <PopularityStars count={song.brand_count} />
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
