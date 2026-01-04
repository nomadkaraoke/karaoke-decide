"use client";

import { useState, useCallback, useEffect } from "react";
import { api } from "@/lib/api";
import {
  SpotifyIcon,
  LastfmIcon,
  ChevronDownIcon,
  XIcon,
  PlusIcon,
} from "@/components/icons";
import { Button, Input, Badge, LoadingPulse } from "@/components/ui";

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

  const handleAddArtist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newArtist.trim()) return;

    try {
      setIsAdding(true);
      setAddError(null);
      await api.my.addDataArtist(newArtist.trim());
      setNewArtist("");
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
      color: "#ff2d92",
      bg: "bg-[#ff2d92]/20",
    },
  };

  const formatPlaycount = (count: number) => {
    if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
    return count.toString();
  };

  return (
    <div className="rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 overflow-hidden">
      {/* Header */}
      <button
        onClick={onToggle}
        aria-expanded={isExpanded}
        className="w-full p-5 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#ff2d92]/20 flex items-center justify-center">
            <span className="text-lg">*</span>
          </div>
          <div>
            <h2 className="font-semibold text-white">Artists You Know</h2>
            <p className="text-sm text-white/60">
              {artists.length === 0
                ? "No artists yet"
                : `${artists.length} artist${artists.length !== 1 ? "s" : ""}`}
            </p>
          </div>
        </div>
        <ChevronDownIcon
          className={`w-5 h-5 text-white/60 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* Content */}
      {isExpanded && (
        <div className="px-5 pb-5 space-y-4">
          {/* Error message */}
          {error && (
            <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
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
              {/* Add artist form */}
              <form onSubmit={handleAddArtist} className="flex gap-2">
                <div className="flex-1">
                  <Input
                    placeholder="Add an artist..."
                    value={newArtist}
                    onChange={(e) => setNewArtist(e.target.value)}
                    error={addError || undefined}
                  />
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
                <div className="text-center py-8 text-white/40 text-sm">
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
                          ? "bg-white/[0.02] opacity-60"
                          : "bg-white/5 hover:bg-white/10"
                      }`}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-white truncate">
                            {artist.artist_name}
                          </span>
                          {/* Source badges */}
                          {artist.sources.map((source) => {
                            const config = sourceConfig[source] || {
                              icon: null,
                              color: "#999",
                              bg: "bg-white/10",
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
                            <span className="text-xs text-orange-400/80 bg-orange-400/10 px-1.5 py-0.5 rounded">
                              Hidden
                            </span>
                          )}
                        </div>
                        {/* Stats line */}
                        <div className="flex items-center gap-2 mt-0.5 text-xs text-white/40">
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
                        className="opacity-0 group-hover:opacity-100 p-1 text-white/40 hover:text-red-400 transition-all"
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
                    <div className="text-center py-2 text-sm text-white/40">
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
