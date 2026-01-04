"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { ProtectedPage } from "@/components/ProtectedPage";
import {
  MusicIcon,
  SparklesIcon,
  SearchIcon,
  PlusIcon,
  CheckIcon,
  TrashIcon,
  XIcon,
  SpotifyIcon,
  LastfmIcon,
  RefreshIcon,
} from "@/components/icons";
import { Button, Input, Badge, LoadingPulse, EmptyState } from "@/components/ui";
import { PopularityStars } from "@/components/SongCard";

// Tabs for the page
type Tab = "artists" | "songs" | "services";

interface UserArtist {
  artist_name: string;
  source: string;
  rank: number;
  time_range: string;
  popularity: number | null;
  genres: string[];
  playcount: number | null;
}

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

interface ConnectedService {
  service_type: string;
  service_username: string;
  last_sync_at: string | null;
  sync_status: string;
  sync_error: string | null;
  tracks_synced: number;
}

export default function MusicIKnowPage() {
  const { isGuest } = useAuth();
  const [activeTab, setActiveTab] = useState<Tab>("artists");

  // Summary stats
  const [stats, setStats] = useState({ artists: 0, songs: 0, services: 0 });
  const [statsLoading, setStatsLoading] = useState(true);

  // Load summary stats
  useEffect(() => {
    const loadStats = async () => {
      try {
        const response = await api.my.getDataSummary();
        setStats({
          artists: response.artists.total,
          songs: response.songs.total,
          services: Object.values(response.services).filter((s) => s.connected).length,
        });
      } catch (err) {
        console.error("Failed to load stats:", err);
      } finally {
        setStatsLoading(false);
      }
    };
    loadStats();
  }, []);

  const tabs = [
    { id: "artists" as Tab, label: "Artists", count: stats.artists },
    { id: "songs" as Tab, label: "Songs", count: stats.songs },
    { id: "services" as Tab, label: "Services", count: stats.services },
  ];

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                <MusicIcon className="w-7 h-7 text-[#ff2d92]" />
                Music I Know
              </h1>
              <p className="text-white/60 text-sm mt-1">
                Artists and songs that power your recommendations
              </p>
            </div>
            <Link href="/recommendations">
              <Button variant="secondary" size="sm">
                <SparklesIcon className="w-4 h-4" />
                Get Recs
              </Button>
            </Link>
          </div>

          {/* Tab Navigation */}
          <div className="flex gap-1 p-1 rounded-xl bg-white/5 mb-6">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`
                  flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-all
                  ${activeTab === tab.id
                    ? "bg-white/10 text-white"
                    : "text-white/50 hover:text-white/70 hover:bg-white/5"
                  }
                `}
              >
                {tab.label}
                {!statsLoading && tab.count > 0 && (
                  <span className={`
                    px-1.5 py-0.5 rounded-full text-xs
                    ${activeTab === tab.id ? "bg-white/20" : "bg-white/10"}
                  `}>
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          {activeTab === "artists" && (
            <ArtistsTab onCountChange={(count) => setStats(s => ({ ...s, artists: count }))} />
          )}
          {activeTab === "songs" && (
            <SongsTab onCountChange={(count) => setStats(s => ({ ...s, songs: count }))} />
          )}
          {activeTab === "services" && (
            <ServicesTab
              isGuest={isGuest}
              onCountChange={(count) => setStats(s => ({ ...s, services: count }))}
            />
          )}

          {/* Footer CTA */}
          <div className="mt-8 pt-6 border-t border-white/10 text-center">
            <p className="text-sm text-white/50 mb-3">
              More music data = better recommendations
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link href="/quiz">
                <Button variant="secondary" size="sm">
                  Take Quiz
                </Button>
              </Link>
              <Link href="/recommendations">
                <Button variant="primary" size="sm">
                  <SparklesIcon className="w-4 h-4" />
                  Get Recommendations
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </main>
    </ProtectedPage>
  );
}

// ============ ARTISTS TAB ============
function ArtistsTab({ onCountChange }: { onCountChange: (count: number) => void }) {
  const [artists, setArtists] = useState<UserArtist[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newArtist, setNewArtist] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [removingArtist, setRemovingArtist] = useState<string | null>(null);

  const loadArtists = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.my.getDataArtists();
      setArtists(response.artists);
      onCountChange(response.artists.length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load artists");
    } finally {
      setIsLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    loadArtists();
  }, [loadArtists]);

  const handleAddArtist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newArtist.trim()) return;

    try {
      setIsAdding(true);
      await api.my.addDataArtist(newArtist.trim());
      setNewArtist("");
      await loadArtists();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add artist");
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
      setError(err instanceof Error ? err.message : "Failed to remove artist");
    } finally {
      setRemovingArtist(null);
    }
  };

  // Group artists by source
  const artistsBySource = artists.reduce((acc, artist) => {
    const source = artist.source;
    if (!acc[source]) acc[source] = [];
    acc[source].push(artist);
    return acc;
  }, {} as Record<string, UserArtist[]>);

  const sourceConfig: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    spotify: { icon: <SpotifyIcon className="w-3 h-3" />, color: "#1DB954", label: "From Spotify" },
    lastfm: { icon: <LastfmIcon className="w-3 h-3" />, color: "#ff4444", label: "From Last.fm" },
    quiz: { icon: <span className="text-xs">✓</span>, color: "#ff2d92", label: "From Quiz" },
  };

  if (isLoading) {
    return <LoadingPulse count={4} />;
  }

  return (
    <div className="space-y-6">
      {/* Add Artist Form */}
      <form onSubmit={handleAddArtist} className="flex gap-2">
        <div className="flex-1 relative">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <PlusIcon className="w-4 h-4 text-white/40" />
          </div>
          <Input
            placeholder="Add an artist you like..."
            value={newArtist}
            onChange={(e) => setNewArtist(e.target.value)}
            className="pl-10"
          />
        </div>
        <Button
          type="submit"
          variant="primary"
          isLoading={isAdding}
          disabled={!newArtist.trim()}
        >
          Add
        </Button>
      </form>

      {/* Error */}
      {error && (
        <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">Dismiss</button>
        </div>
      )}

      {/* Artists List */}
      {artists.length === 0 ? (
        <EmptyState
          icon={<MusicIcon className="w-8 h-8 text-white/20" />}
          title="No artists yet"
          description="Add artists you like or take the quiz to get started"
          action={{ label: "Take Quiz", onClick: () => window.location.href = "/quiz" }}
        />
      ) : (
        <div className="space-y-6">
          {Object.entries(artistsBySource).map(([source, sourceArtists]) => {
            const config = sourceConfig[source] || { icon: null, color: "#999", label: source };

            return (
              <div key={source}>
                <div className="flex items-center gap-2 mb-3">
                  <div
                    className="w-6 h-6 rounded-full flex items-center justify-center"
                    style={{ backgroundColor: `${config.color}20` }}
                  >
                    <span style={{ color: config.color }}>{config.icon}</span>
                  </div>
                  <span className="text-sm font-medium text-white/70">{config.label}</span>
                  <Badge variant="default">{sourceArtists.length}</Badge>
                </div>

                <div className="flex flex-wrap gap-2">
                  {sourceArtists.map((artist) => (
                    <div
                      key={`${source}-${artist.artist_name}`}
                      className="group flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/10 text-sm text-white/80 hover:bg-white/15 transition-colors"
                    >
                      <span>{artist.artist_name}</span>
                      {artist.rank && artist.rank <= 10 && (
                        <span className="text-xs opacity-60" style={{ color: config.color }}>
                          #{artist.rank}
                        </span>
                      )}
                      <button
                        onClick={() => handleRemoveArtist(artist.artist_name)}
                        disabled={removingArtist === artist.artist_name}
                        className="opacity-0 group-hover:opacity-100 text-white/40 hover:text-red-400 transition-all"
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
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ============ SONGS TAB ============
function SongsTab({ onCountChange }: { onCountChange: (count: number) => void }) {
  const [knownSongs, setKnownSongs] = useState<KnownSong[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removingIds, setRemovingIds] = useState<Set<string>>(new Set());

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<CatalogSong[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [addedSongIds, setAddedSongIds] = useState<Set<number>>(new Set());
  const [addingIds, setAddingIds] = useState<Set<number>>(new Set());
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const loadKnownSongs = useCallback(async (pageNum: number, append: boolean = false) => {
    try {
      if (append) setIsLoadingMore(true);
      else setIsLoading(true);
      setError(null);

      const response = await api.knownSongs.list(pageNum, 20);

      if (append) {
        setKnownSongs((prev) => [...prev, ...response.songs]);
      } else {
        setKnownSongs(response.songs);
      }
      setTotal(response.total);
      setHasMore(pageNum * response.per_page < response.total);
      setPage(pageNum);
      onCountChange(response.total);

      const songIds = new Set(response.songs.map((s) => parseInt(s.song_id)));
      if (append) {
        setAddedSongIds((prev) => new Set([...prev, ...songIds]));
      } else {
        setAddedSongIds(songIds);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load songs");
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    loadKnownSongs(1);
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, [loadKnownSongs]);

  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      setSearchResults([]);
      return;
    }
    setIsSearching(true);
    try {
      const response = await api.catalog.searchSongs(query, 8);
      setSearchResults(response.songs);
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

  const handleAddSong = async (song: CatalogSong) => {
    if (addedSongIds.has(song.id) || addingIds.has(song.id)) return;
    setAddingIds((prev) => new Set([...prev, song.id]));
    try {
      const result = await api.knownSongs.add(song.id);
      if (result.added || result.already_existed) {
        setAddedSongIds((prev) => new Set([...prev, song.id]));
        if (result.added) loadKnownSongs(1);
      }
    } finally {
      setAddingIds((prev) => {
        const next = new Set(prev);
        next.delete(song.id);
        return next;
      });
    }
  };

  const handleRemoveSong = async (song: KnownSong) => {
    const songId = parseInt(song.song_id);
    setRemovingIds((prev) => new Set([...prev, song.id]));
    try {
      await api.knownSongs.remove(songId);
      setKnownSongs((prev) => prev.filter((s) => s.id !== song.id));
      setTotal((prev) => {
        const newTotal = prev - 1;
        onCountChange(newTotal);
        return newTotal;
      });
      setAddedSongIds((prev) => {
        const next = new Set(prev);
        next.delete(songId);
        return next;
      });
    } finally {
      setRemovingIds((prev) => {
        const next = new Set(prev);
        next.delete(song.id);
        return next;
      });
    }
  };

  if (isLoading) {
    return <LoadingPulse count={4} />;
  }

  return (
    <div className="space-y-6">
      {/* Search to Add */}
      <div>
        <div className="relative">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <SearchIcon className="w-4 h-4 text-white/40" />
          </div>
          <Input
            type="text"
            placeholder="Search songs to add..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="pl-10"
          />
        </div>

        {/* Search Results */}
        {searchQuery.trim() && (
          <div className="mt-3">
            {isSearching ? (
              <div className="flex items-center justify-center py-6">
                <div className="animate-spin w-5 h-5 border-2 border-[#00f5ff] border-t-transparent rounded-full" />
              </div>
            ) : searchResults.length === 0 ? (
              <p className="text-white/40 text-sm text-center py-4">
                No songs found for &quot;{searchQuery}&quot;
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {searchResults.map((song) => {
                  const isAdded = addedSongIds.has(song.id);
                  const isAdding = addingIds.has(song.id);
                  return (
                    <div
                      key={song.id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-white/5 border border-white/10"
                    >
                      <div className="flex-1 min-w-0">
                        <h3 className="text-white font-medium truncate">{song.title}</h3>
                        <p className="text-white/60 text-sm truncate">{song.artist}</p>
                      </div>
                      <PopularityStars count={song.brand_count} />
                      <button
                        onClick={() => handleAddSong(song)}
                        disabled={isAdded || isAdding}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
                          isAdded
                            ? "bg-green-500/20 text-green-400"
                            : isAdding
                            ? "bg-white/10 text-white/40"
                            : "bg-[#ff2d92]/20 text-[#ff2d92] hover:bg-[#ff2d92]/30"
                        }`}
                      >
                        {isAdded ? (
                          <><CheckIcon className="w-4 h-4" /> Added</>
                        ) : isAdding ? (
                          <><div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" /> Adding</>
                        ) : (
                          <><PlusIcon className="w-4 h-4" /> Add</>
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
      {searchQuery.trim() && <div className="border-t border-white/10" />}

      {/* Error */}
      {error && (
        <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Known Songs List */}
      {knownSongs.length === 0 && !searchQuery ? (
        <EmptyState
          icon={<MusicIcon className="w-8 h-8 text-white/20" />}
          title="No songs yet"
          description="Search above to add songs you know and love to sing!"
        />
      ) : knownSongs.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-white/70">My Songs</h2>
            <span className="text-white/40 text-sm">{total} songs</span>
          </div>

          <div className="flex flex-col gap-2">
            {knownSongs.map((song) => {
              const isRemoving = removingIds.has(song.id);
              return (
                <div
                  key={song.id}
                  className={`flex items-center gap-3 p-3 rounded-xl bg-white/5 border border-white/10 transition-opacity ${
                    isRemoving ? "opacity-50" : ""
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <h3 className="text-white font-medium truncate">{song.title}</h3>
                    <p className="text-white/60 text-sm truncate">{song.artist}</p>
                  </div>
                  <button
                    onClick={() => handleRemoveSong(song)}
                    disabled={isRemoving}
                    className="p-2 rounded-full text-white/40 hover:text-red-400 hover:bg-red-400/10 transition-colors"
                    title="Remove song"
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

          {hasMore && (
            <div className="text-center">
              <Button variant="secondary" onClick={() => loadKnownSongs(page + 1, true)} isLoading={isLoadingMore}>
                Load More
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ============ SERVICES TAB ============
function ServicesTab({
  isGuest,
  onCountChange
}: {
  isGuest: boolean;
  onCountChange: (count: number) => void;
}) {
  const [services, setServices] = useState<ConnectedService[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastfmUsername, setLastfmUsername] = useState("");
  const [isConnectingLastfm, setIsConnectingLastfm] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState<string | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  const loadServices = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.services.getSyncStatus();
      setServices(response.services);
      onCountChange(response.services.length);

      if (response.active_job && ["pending", "in_progress"].includes(response.active_job.status)) {
        setIsSyncing(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load services");
    } finally {
      setIsLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    if (!isGuest) loadServices();
    else setIsLoading(false);
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [loadServices, isGuest]);

  const isConnected = (type: string) => services.some((s) => s.service_type === type);
  const getService = (type: string) => services.find((s) => s.service_type === type);

  const handleConnectSpotify = async () => {
    try {
      const response = await api.services.connectSpotify();
      window.location.href = response.auth_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect Spotify");
    }
  };

  const handleConnectLastfm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!lastfmUsername.trim()) return;
    try {
      setIsConnectingLastfm(true);
      await api.services.connectLastfm(lastfmUsername);
      setLastfmUsername("");
      await loadServices();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to connect Last.fm");
    } finally {
      setIsConnectingLastfm(false);
    }
  };

  const handleDisconnect = async (serviceType: string) => {
    try {
      setDisconnecting(serviceType);
      await api.services.disconnect(serviceType);
      await loadServices();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to disconnect ${serviceType}`);
    } finally {
      setDisconnecting(null);
    }
  };

  const handleSync = async () => {
    try {
      setIsSyncing(true);
      setSyncMessage(null);
      setError(null);
      await api.services.sync();

      // Simple polling for completion
      const checkStatus = async () => {
        const response = await api.services.getSyncStatus();
        setServices(response.services);

        if (response.active_job?.status === "completed") {
          setIsSyncing(false);
          const totalMatched = response.active_job.results?.reduce((sum, r) => sum + r.tracks_matched, 0) || 0;
          setSyncMessage(`Sync complete! Found ${totalMatched} karaoke songs.`);
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        } else if (response.active_job?.status === "failed") {
          setIsSyncing(false);
          setError(response.active_job.error || "Sync failed");
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        }
      };

      pollIntervalRef.current = setInterval(checkStatus, 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start sync");
      setIsSyncing(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  if (isGuest) {
    return (
      <div className="text-center py-12">
        <div className="w-16 h-16 rounded-full bg-white/5 flex items-center justify-center mx-auto mb-4">
          <SpotifyIcon className="w-8 h-8 text-white/20" />
        </div>
        <h3 className="text-lg font-medium text-white mb-2">Connect Your Music Services</h3>
        <p className="text-white/60 text-sm mb-6 max-w-sm mx-auto">
          Create an account to connect Spotify and Last.fm for personalized recommendations based on your listening history.
        </p>
        <Link href="/login">
          <Button variant="primary">Create Account</Button>
        </Link>
      </div>
    );
  }

  if (isLoading) {
    return <LoadingPulse count={2} />;
  }

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Sync Message */}
      {syncMessage && (
        <div className="p-3 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400 text-sm">
          {syncMessage}
        </div>
      )}

      {/* Spotify */}
      <div className="p-4 rounded-xl bg-white/5 border border-white/10">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-full bg-[#1DB954]/20 flex items-center justify-center">
            <SpotifyIcon className="w-5 h-5 text-[#1DB954]" />
          </div>
          <div className="flex-1">
            <h3 className="font-medium text-white">Spotify</h3>
            {isConnected("spotify") && (
              <p className="text-xs text-white/60">{getService("spotify")?.service_username}</p>
            )}
          </div>
          {isConnected("spotify") ? (
            <Badge variant="success">Connected</Badge>
          ) : (
            <Badge variant="default">Not connected</Badge>
          )}
        </div>

        {isConnected("spotify") ? (
          <div className="space-y-2">
            <div className="flex items-center gap-4 text-xs text-white/50">
              <span>{getService("spotify")?.tracks_synced || 0} tracks</span>
              {getService("spotify")?.last_sync_at && (
                <span>Last sync: {formatDate(getService("spotify")!.last_sync_at!)}</span>
              )}
            </div>
            <Button variant="danger" size="sm" onClick={() => handleDisconnect("spotify")} isLoading={disconnecting === "spotify"}>
              Disconnect
            </Button>
          </div>
        ) : (
          <Button variant="primary" size="sm" onClick={handleConnectSpotify} leftIcon={<SpotifyIcon className="w-4 h-4" />}>
            Connect Spotify
          </Button>
        )}
      </div>

      {/* Last.fm */}
      <div className="p-4 rounded-xl bg-white/5 border border-white/10">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-full bg-[#D51007]/20 flex items-center justify-center">
            <LastfmIcon className="w-5 h-5 text-[#ff4444]" />
          </div>
          <div className="flex-1">
            <h3 className="font-medium text-white">Last.fm</h3>
            {isConnected("lastfm") && (
              <p className="text-xs text-white/60">{getService("lastfm")?.service_username}</p>
            )}
          </div>
          {isConnected("lastfm") ? (
            <Badge variant="success">Connected</Badge>
          ) : (
            <Badge variant="default">Not connected</Badge>
          )}
        </div>

        {isConnected("lastfm") ? (
          <div className="space-y-2">
            <div className="flex items-center gap-4 text-xs text-white/50">
              <span>{getService("lastfm")?.tracks_synced || 0} tracks</span>
              {getService("lastfm")?.last_sync_at && (
                <span>Last sync: {formatDate(getService("lastfm")!.last_sync_at!)}</span>
              )}
            </div>
            <Button variant="danger" size="sm" onClick={() => handleDisconnect("lastfm")} isLoading={disconnecting === "lastfm"}>
              Disconnect
            </Button>
          </div>
        ) : (
          <form onSubmit={handleConnectLastfm} className="space-y-2">
            <Input
              placeholder="Your Last.fm username"
              value={lastfmUsername}
              onChange={(e) => setLastfmUsername(e.target.value)}
            />
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isLoading={isConnectingLastfm}
              disabled={!lastfmUsername.trim()}
              leftIcon={<LastfmIcon className="w-4 h-4" />}
            >
              Connect Last.fm
            </Button>
          </form>
        )}
      </div>

      {/* Sync Button */}
      {services.length > 0 && (
        <div className="p-4 rounded-xl bg-white/5 border border-white/10">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-medium text-white text-sm">Sync listening history</h3>
              <p className="text-xs text-white/50">
                {isSyncing ? "Syncing in background..." : "Fetch latest from services"}
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleSync}
              isLoading={isSyncing}
              disabled={isSyncing}
              leftIcon={<RefreshIcon className="w-4 h-4" />}
            >
              {isSyncing ? "Syncing..." : "Sync Now"}
            </Button>
          </div>
        </div>
      )}

      {/* Tip for Spotify-only users */}
      {isConnected("spotify") && !isConnected("lastfm") && (
        <div className="p-3 rounded-xl bg-[#ff4444]/10 border border-[#ff4444]/20">
          <p className="text-xs text-white/70">
            <strong className="text-[#ff4444]">Tip:</strong> Connect Last.fm for better recommendations based on your full listening history.
          </p>
        </div>
      )}
    </div>
  );
}
