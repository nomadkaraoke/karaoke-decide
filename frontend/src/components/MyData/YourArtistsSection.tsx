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
  source: string;
  rank: number;
  time_range: string;
  popularity: number | null;
  genres: string[];
  playcount: number | null;
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

  // Group artists by source
  const artistsBySource = artists.reduce(
    (acc, artist) => {
      const source = artist.source;
      if (!acc[source]) acc[source] = [];
      acc[source].push(artist);
      return acc;
    },
    {} as Record<string, UserArtist[]>
  );

  const sourceConfig: Record<
    string,
    { icon: React.ReactNode; color: string; label: string }
  > = {
    spotify: {
      icon: <SpotifyIcon className="w-3 h-3" />,
      color: "#1DB954",
      label: "Spotify",
    },
    lastfm: {
      icon: <LastfmIcon className="w-3 h-3" />,
      color: "#ff4444",
      label: "Last.fm",
    },
    quiz: {
      icon: <span className="text-xs">Q</span>,
      color: "#ff2d92",
      label: "Quiz / Manual",
    },
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

              {/* Artist list by source */}
              {artists.length === 0 ? (
                <div className="text-center py-8 text-white/40 text-sm">
                  <p>No artists yet.</p>
                  <p className="mt-1">
                    Add artists manually, take the quiz, or sync your music
                    services.
                  </p>
                </div>
              ) : (
                <div className="space-y-4">
                  {Object.entries(artistsBySource).map(
                    ([source, sourceArtists]) => {
                      const config = sourceConfig[source] || {
                        icon: null,
                        color: "#999",
                        label: source,
                      };

                      return (
                        <div key={source}>
                          <div className="flex items-center gap-2 mb-2">
                            <div
                              className="w-5 h-5 rounded-full flex items-center justify-center"
                              style={{ backgroundColor: `${config.color}20` }}
                            >
                              <span style={{ color: config.color }}>
                                {config.icon}
                              </span>
                            </div>
                            <span className="text-sm font-medium text-white/70">
                              {config.label}
                            </span>
                            <Badge variant="default">{sourceArtists.length}</Badge>
                          </div>

                          <div className="flex flex-wrap gap-2">
                            {sourceArtists.slice(0, 30).map((artist) => (
                              <div
                                key={`${source}-${artist.artist_name}`}
                                className="group flex items-center gap-1 px-2 py-1 rounded-full bg-white/10 text-sm text-white/80 hover:bg-white/20 transition-colors"
                              >
                                <span>{artist.artist_name}</span>
                                {/* Show rank for top 10 */}
                                {artist.rank && artist.rank <= 10 && (
                                  <span
                                    className="text-xs"
                                    style={{ color: config.color }}
                                  >
                                    #{artist.rank}
                                  </span>
                                )}
                                {/* Show playcount pill (primarily Last.fm data) */}
                                {artist.playcount && artist.playcount > 0 && (
                                  <span className="text-xs px-1.5 py-0.5 rounded bg-white/10 text-white/50">
                                    {artist.playcount.toLocaleString()} plays
                                  </span>
                                )}
                                {/* Show popularity pill for Spotify artists */}
                                {source === "spotify" && artist.popularity !== null && artist.popularity > 0 && (
                                  <span className="text-xs px-1.5 py-0.5 rounded bg-[#1DB954]/20 text-[#1DB954]/80">
                                    Pop: {artist.popularity}
                                  </span>
                                )}
                                <button
                                  onClick={() =>
                                    handleRemoveArtist(artist.artist_name)
                                  }
                                  disabled={removingArtist === artist.artist_name}
                                  className="ml-1 opacity-0 group-hover:opacity-100 text-white/40 hover:text-red-400 transition-all"
                                  title="Remove artist"
                                >
                                  {removingArtist === artist.artist_name ? (
                                    <span className="animate-pulse">...</span>
                                  ) : (
                                    <XIcon className="w-3 h-3" />
                                  )}
                                </button>
                              </div>
                            ))}
                            {sourceArtists.length > 30 && (
                              <span className="px-2 py-1 text-sm text-white/40">
                                +{sourceArtists.length - 30} more
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    }
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
