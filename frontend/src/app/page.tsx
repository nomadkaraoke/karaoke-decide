"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { SongCard } from "@/components/SongCard";
import { SearchIcon } from "@/components/icons";
import { LoadingPulse } from "@/components/ui";

interface Song {
  id: number;
  artist: string;
  title: string;
  brandCount: number;
}

export default function Home() {
  const [searchQuery, setSearchQuery] = useState("");
  const [songs, setSongs] = useState<Song[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSearching, setIsSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load popular songs on mount
  useEffect(() => {
    const loadPopular = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const popularSongs = await api.catalog.getPopularSongs(20);
        setSongs(
          popularSongs.map((s) => ({
            id: s.id,
            artist: s.artist,
            title: s.title,
            brandCount: s.brand_count,
          }))
        );
      } catch (err) {
        setError("Failed to load songs. Please try again.");
        console.error("Error loading popular songs:", err);
      } finally {
        setIsLoading(false);
      }
    };
    loadPopular();
  }, []);

  // Search handler
  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      // Reset to popular songs
      try {
        setIsSearching(true);
        const popularSongs = await api.catalog.getPopularSongs(20);
        setSongs(
          popularSongs.map((s) => ({
            id: s.id,
            artist: s.artist,
            title: s.title,
            brandCount: s.brand_count,
          }))
        );
        setHasSearched(false);
      } catch (err) {
        console.error("Error loading popular songs:", err);
      } finally {
        setIsSearching(false);
      }
      return;
    }

    setIsSearching(true);
    setHasSearched(true);
    setError(null);

    try {
      const results = await api.catalog.searchSongs(query, 30);
      setSongs(
        results.songs.map((s) => ({
          id: s.id,
          artist: s.artist,
          title: s.title,
          brandCount: s.brand_count,
        }))
      );
    } catch (err) {
      setError("Search failed. Please try again.");
      console.error("Error searching songs:", err);
    } finally {
      setIsSearching(false);
    }
  }, []);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      handleSearch(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, handleSearch]);

  return (
    <main className="relative min-h-screen pb-safe">
      {/* Content */}
      <div className="max-w-2xl mx-auto px-4 py-6">
        {/* Search bar */}
        <div className="relative group mb-6">
          <div className="absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-[#ff2d92] via-[#b347ff] to-[#00f5ff] opacity-30 blur-sm group-focus-within:opacity-60 transition-opacity" />
          <div className="relative flex items-center">
            <SearchIcon className="absolute left-4 w-5 h-5 text-white/40 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search songs or artists..."
              className="w-full pl-12 pr-4 py-3.5 rounded-2xl bg-[rgba(20,20,30,0.95)] border border-white/10 text-white placeholder-white/40 text-base focus:outline-none focus:border-white/20 transition-colors"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-4 w-6 h-6 flex items-center justify-center rounded-full bg-white/10 text-white/60 hover:bg-white/20 hover:text-white transition-colors"
              >
                <span className="text-sm">×</span>
              </button>
            )}
          </div>
        </div>

        {/* Section title */}
        <div className="flex items-center gap-3 mb-4">
          <h2 className="text-lg font-semibold text-white/80">
            {hasSearched
              ? songs.length > 0
                ? `Results for "${searchQuery}"`
                : "No songs found"
              : "Popular Karaoke Songs"}
          </h2>
          {!hasSearched && (
            <span className="px-2 py-0.5 rounded-full bg-[#ff2d92]/20 text-[#ff2d92] text-xs font-medium">
              HOT
            </span>
          )}
        </div>

        {/* Song list */}
        {isLoading || isSearching ? (
          <LoadingPulse />
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
              <span className="text-2xl">⚠️</span>
            </div>
            <p className="text-white/60 mb-2">{error}</p>
            <button
              onClick={() => handleSearch(searchQuery)}
              className="mt-2 px-4 py-2 rounded-full bg-white/10 text-white/80 text-sm hover:bg-white/20 transition-colors"
            >
              Try again
            </button>
          </div>
        ) : songs.length > 0 ? (
          <div className="flex flex-col gap-3">
            {songs.map((song, index) => (
              <SongCard key={song.id} song={song} index={index} />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-full bg-white/5 flex items-center justify-center mb-4">
              <SearchIcon className="w-8 h-8 text-white/20" />
            </div>
            <p className="text-white/40 mb-2">No songs found</p>
            <p className="text-white/20 text-sm">Try a different search term</p>
          </div>
        )}

        {/* Footer hint */}
        <div className="mt-8 text-center">
          <p className="text-white/30 text-sm">
            275,000+ karaoke songs available
          </p>
          <p className="text-white/20 text-xs mt-1">
            Powered by KaraokeNerds + Spotify data
          </p>
        </div>
      </div>
    </main>
  );
}
