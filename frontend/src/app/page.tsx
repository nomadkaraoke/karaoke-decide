"use client";

import { useState, useEffect, useCallback } from "react";

// API configuration
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "https://karaoke-decide-718638054799.us-central1.run.app";

interface Song {
  id: number;
  artist: string;
  title: string;
  brandCount: number;
}

interface ApiSong {
  id: number;
  artist: string;
  title: string;
  brand_count: number;
  brands: string[];
  is_popular: boolean;
}

interface ApiSearchResponse {
  songs: ApiSong[];
  total: number;
  page: number;
  per_page: number;
  has_more: boolean;
}

// Convert API response to our Song type
function mapApiSong(apiSong: ApiSong): Song {
  return {
    id: apiSong.id,
    artist: apiSong.artist,
    title: apiSong.title,
    brandCount: apiSong.brand_count,
  };
}

// API client functions
async function fetchPopularSongs(limit: number = 20): Promise<Song[]> {
  const response = await fetch(`${API_BASE_URL}/api/catalog/songs/popular?limit=${limit}`);
  if (!response.ok) throw new Error("Failed to fetch popular songs");
  const data: ApiSong[] = await response.json();
  return data.map(mapApiSong);
}

async function searchSongs(query: string, limit: number = 20): Promise<Song[]> {
  const response = await fetch(`${API_BASE_URL}/api/catalog/songs?q=${encodeURIComponent(query)}&per_page=${limit}`);
  if (!response.ok) throw new Error("Failed to search songs");
  const data: ApiSearchResponse = await response.json();
  return data.songs.map(mapApiSong);
}

function MicrophoneIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  );
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function StarIcon({ filled, className }: { filled: boolean; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
    </svg>
  );
}

function PopularityStars({ count }: { count: number }) {
  // Convert brand count to 1-5 stars
  const stars = Math.min(5, Math.max(1, Math.ceil(count / 12)));
  return (
    <div className="flex items-center gap-0.5">
      {[...Array(5)].map((_, i) => (
        <StarIcon
          key={i}
          filled={i < stars}
          className={`w-3.5 h-3.5 ${i < stars ? "text-[#ffeb3b]" : "text-white/20"}`}
        />
      ))}
      <span className="ml-1.5 text-xs text-white/40 font-mono">{count}</span>
    </div>
  );
}

function SongCard({ song, index }: { song: Song; index: number }) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className="group relative animate-fade-in-up"
      style={{ animationDelay: `${index * 50}ms` }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Glow effect on hover */}
      <div
        className={`absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-[#ff2d92] via-[#b347ff] to-[#00f5ff] opacity-0 blur-sm transition-opacity duration-300 ${
          isHovered ? "opacity-60" : ""
        }`}
      />

      <div className="relative flex flex-col gap-3 p-4 rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 backdrop-blur-sm transition-all duration-300 hover:border-white/20">
        {/* Song info */}
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold text-white truncate group-hover:text-[#00f5ff] transition-colors">
            {song.title}
          </h3>
          <p className="text-sm text-white/60 truncate mt-0.5">{song.artist}</p>
        </div>

        {/* Bottom row */}
        <div className="flex items-center justify-between gap-3">
          <PopularityStars count={song.brandCount} />

          <button
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-gradient-to-r from-[#ff2d92] to-[#b347ff] text-white text-sm font-semibold transition-all duration-200 hover:scale-105 hover:shadow-[0_0_20px_rgba(255,45,146,0.5)] active:scale-95"
            onClick={() => {
              // Will link to karaoke video or generator
              window.open(
                `https://www.youtube.com/results?search_query=${encodeURIComponent(
                  `${song.artist} ${song.title} karaoke`
                )}`,
                "_blank"
              );
            }}
          >
            <MicrophoneIcon className="w-4 h-4" />
            <span>Sing it!</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function LoadingPulse() {
  return (
    <div className="flex flex-col gap-4">
      {[...Array(4)].map((_, i) => (
        <div
          key={i}
          className="h-28 rounded-2xl bg-white/5 animate-pulse"
          style={{ animationDelay: `${i * 100}ms` }}
        />
      ))}
    </div>
  );
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
        const popularSongs = await fetchPopularSongs(20);
        setSongs(popularSongs);
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
        const popularSongs = await fetchPopularSongs(20);
        setSongs(popularSongs);
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
      const results = await searchSongs(query, 30);
      setSongs(results);
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
      {/* Header */}
      <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0a0a0f]/80 border-b border-white/5">
        <div className="max-w-2xl mx-auto px-4 py-4">
          {/* Logo */}
          <div className="flex items-center justify-center gap-3 mb-4">
            <div className="relative">
              <MicrophoneIcon className="w-8 h-8 text-[#ff2d92]" />
              <div className="absolute inset-0 blur-md bg-[#ff2d92]/50" />
            </div>
            <h1 className="text-2xl font-bold tracking-tight">
              <span className="text-white">Nomad</span>
              <span className="text-[#ff2d92] neon-text-pink ml-1">Karaoke</span>
            </h1>
          </div>

          {/* Search bar */}
          <div className="relative group">
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
        </div>
      </header>

      {/* Content */}
      <div className="max-w-2xl mx-auto px-4 py-6">
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
