"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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
  SpotifyIcon,
  LastfmIcon,
  RefreshIcon,
  EyeIcon,
  EyeOffIcon,
  MicrophoneIcon,
} from "@/components/icons";
import { EnjoySingingModal } from "@/components/EnjoySingingModal";
import { Button, Input, Badge, LoadingPulse, EmptyState } from "@/components/ui";
import { PopularityStars } from "@/components/SongCard";

// Tabs for the page
type Tab = "artists" | "songs" | "services";

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
  // Enjoy singing fields
  enjoy_singing?: boolean;
  singing_tags?: string[];
  singing_energy?: string | null;
  vocal_comfort?: string | null;
  notes?: string | null;
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
  const t = useTranslations("musicIKnow");
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
          songs: response.songs.known_songs,
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

  // Stable callbacks for child components to avoid infinite re-render loops
  // (inline arrow functions create new references on every render)
  const handleArtistsCountChange = useCallback((count: number) => {
    setStats(s => ({ ...s, artists: count }));
  }, []);

  const handleSongsCountChange = useCallback((count: number) => {
    setStats(s => ({ ...s, songs: count }));
  }, []);

  const handleServicesCountChange = useCallback((count: number) => {
    setStats(s => ({ ...s, services: count }));
  }, []);

  const tabs = [
    { id: "artists" as Tab, label: t("artistsTab"), count: stats.artists },
    { id: "songs" as Tab, label: t("songsTab"), count: stats.songs },
    { id: "services" as Tab, label: t("servicesTab"), count: stats.services },
  ];

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
                <MusicIcon className="w-7 h-7 text-[var(--brand-pink)]" />
                {t("title")}
              </h1>
              <p className="text-[var(--text-muted)] text-sm mt-1">
                {t("subtitle")}
              </p>
            </div>
            <Link href="/recommendations">
              <Button variant="secondary" size="sm">
                <SparklesIcon className="w-4 h-4" />
                {t("getRecs")}
              </Button>
            </Link>
          </div>

          {/* Tab Navigation */}
          <div className="flex gap-1 p-1 rounded-xl bg-[var(--card)] mb-6">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`
                  flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg text-sm font-medium transition-all
                  ${activeTab === tab.id
                    ? "bg-[var(--secondary)] text-[var(--text)]"
                    : "text-[var(--text-subtle)] hover:text-[var(--text-muted)] hover:bg-[var(--card)]"
                  }
                `}
              >
                {tab.label}
                {!statsLoading && tab.count > 0 && (
                  <span className={`
                    px-1.5 py-0.5 rounded-full text-xs
                    ${activeTab === tab.id ? "bg-[var(--secondary)]" : "bg-[var(--secondary)]"}
                  `}>
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          {activeTab === "artists" && (
            <ArtistsTab onCountChange={handleArtistsCountChange} />
          )}
          {activeTab === "songs" && (
            <SongsTab onCountChange={handleSongsCountChange} />
          )}
          {activeTab === "services" && (
            <ServicesTab
              isGuest={isGuest}
              onCountChange={handleServicesCountChange}
            />
          )}

          {/* Footer CTA */}
          <div className="mt-8 pt-6 border-t border-[var(--card-border)] text-center">
            <p className="text-sm text-[var(--text-subtle)] mb-3">
              {t("moreDataBetterRecs")}
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link href="/quiz">
                <Button variant="secondary" size="sm">
                  {t("takeQuiz")}
                </Button>
              </Link>
              <Link href="/recommendations">
                <Button variant="primary" size="sm">
                  <SparklesIcon className="w-4 h-4" />
                  {t("getRecommendations")}
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
  const t = useTranslations("musicIKnow");
  const tCommon = useTranslations("common");
  const [artists, setArtists] = useState<UserArtist[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newArtist, setNewArtist] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);

  const PER_PAGE = 100;

  const loadArtists = useCallback(async (pageNum: number, append: boolean = false) => {
    try {
      if (append) setIsLoadingMore(true);
      else setIsLoading(true);
      setError(null);

      const response = await api.my.getDataArtists(pageNum, PER_PAGE);

      if (append) {
        setArtists((prev) => [...prev, ...response.artists]);
      } else {
        setArtists(response.artists);
      }
      setTotal(response.total);
      setPage(response.page);
      setHasMore(response.has_more);
      onCountChange(response.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("failedToLoadArtists"));
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  }, [onCountChange, t]);

  useEffect(() => {
    loadArtists(1);
  }, [loadArtists]);

  const handleAddArtist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newArtist.trim()) return;

    try {
      setIsAdding(true);
      await api.my.addDataArtist(newArtist.trim());
      setNewArtist("");
      await loadArtists(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("failedToAddArtist"));
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemoveArtist = async (artistName: string) => {
    try {
      setActionInProgress(artistName);
      await api.my.removeDataArtist(artistName);
      // Optimistically update local state
      setArtists((prev) => prev.filter((a) => a.artist_name !== artistName));
      setTotal((prev) => {
        const newTotal = prev - 1;
        onCountChange(newTotal);
        return newTotal;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("failedToRemoveArtist"));
    } finally {
      setActionInProgress(null);
    }
  };

  const handleExcludeArtist = async (artistName: string) => {
    try {
      setActionInProgress(artistName);
      await api.my.excludeArtist(artistName);
      // Optimistically update local state
      setArtists((prev) =>
        prev.map((a) =>
          a.artist_name === artistName ? { ...a, is_excluded: true } : a
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("failedToExcludeArtist"));
    } finally {
      setActionInProgress(null);
    }
  };

  const handleIncludeArtist = async (artistName: string) => {
    try {
      setActionInProgress(artistName);
      await api.my.includeArtist(artistName);
      // Optimistically update local state
      setArtists((prev) =>
        prev.map((a) =>
          a.artist_name === artistName ? { ...a, is_excluded: false } : a
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("failedToIncludeArtist"));
    } finally {
      setActionInProgress(null);
    }
  };

  // Format playcount for display
  const formatPlaycount = (count: number) => {
    if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
    return count.toString();
  };

  // Get time range display name
  const getTimeRangeLabel = (range: string | null) => {
    if (!range) return null;
    switch (range) {
      case "short_term": return t("spotifyTimeRangeShort");
      case "medium_term": return t("spotifyTimeRangeMedium");
      case "long_term": return t("spotifyTimeRangeLong");
      default: return range;
    }
  };

  if (isLoading) {
    return <LoadingPulse count={4} />;
  }

  return (
    <div className="space-y-4">
      {/* Add Artist Form */}
      <form onSubmit={handleAddArtist} className="flex gap-2">
        <div className="flex-1 relative">
          <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
            <PlusIcon className="w-4 h-4 text-[var(--text-subtle)]" />
          </div>
          <Input
            placeholder={t("addArtistPlaceholder")}
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
          {tCommon("add")}
        </Button>
      </form>

      {/* Error */}
      {error && (
        <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">{tCommon("dismiss")}</button>
        </div>
      )}

      {/* Artists List */}
      {artists.length === 0 ? (
        <EmptyState
          icon={<MusicIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
          title={t("noArtistsYet")}
          description={t("noArtistsDesc")}
          action={{ label: t("takeQuiz"), onClick: () => window.location.href = "/quiz" }}
        />
      ) : (
        <>
          {/* Header with count */}
          <div className="flex items-center justify-between text-sm text-[var(--text-muted)]">
            <span>{t("showingOf", { current: artists.length, total })}</span>
          </div>

          {/* Artist Rows - Compact single-line layout */}
          <div className="space-y-1">
            {artists.map((artist) => {
              const isProcessing = actionInProgress === artist.artist_name;
              const isExcluded = artist.is_excluded;

              return (
                <div
                  key={artist.artist_name}
                  className={`py-2 px-3 rounded-lg border transition-all ${
                    isExcluded
                      ? "bg-white/[0.02] border-white/5 opacity-60"
                      : "bg-[var(--card)] border-[var(--card-border)]"
                  } ${isProcessing ? "opacity-50" : ""}`}
                >
                  <div className="flex items-center gap-2">
                    {/* Artist Name */}
                    <span className={`font-medium truncate ${isExcluded ? "text-[var(--text-subtle)]" : "text-[var(--text)]"}`}>
                      {artist.artist_name}
                    </span>

                    {/* Hidden badge */}
                    {isExcluded && (
                      <span className="text-[10px] text-orange-400/80 bg-orange-400/10 px-1 py-0.5 rounded shrink-0">
                        {t("hidden")}
                      </span>
                    )}

                    {/* Source badges - compact icons only */}
                    <div className="flex items-center gap-1 shrink-0">
                      {artist.sources.map((source) => {
                        const config: Record<string, { icon: React.ReactNode; color: string; title: string }> = {
                          spotify: { icon: <SpotifyIcon className="w-3.5 h-3.5" />, color: "#1DB954", title: "Spotify" },
                          lastfm: { icon: <LastfmIcon className="w-3.5 h-3.5" />, color: "#ff4444", title: "Last.fm" },
                          quiz: { icon: <CheckIcon className="w-3.5 h-3.5" />, color: "var(--brand-pink)", title: "Quiz/Manual" },
                        };
                        const sourceConfig = config[source];
                        if (!sourceConfig) return null;
                        return (
                          <span
                            key={source}
                            style={{ color: sourceConfig.color }}
                            title={sourceConfig.title}
                          >
                            {sourceConfig.icon}
                          </span>
                        );
                      })}
                    </div>

                    {/* Stats - inline, text only */}
                    <div className="flex items-center gap-1.5 text-xs text-[var(--text-subtle)] shrink-0 ml-auto">
                      {/* Spotify rank */}
                      {artist.spotify_rank && (
                        <span title={`Spotify Top ${artist.spotify_rank}${artist.spotify_time_range ? ` (${getTimeRangeLabel(artist.spotify_time_range)})` : ""}`}>
                          #{artist.spotify_rank}
                        </span>
                      )}

                      {/* Last.fm playcount */}
                      {artist.lastfm_playcount && artist.lastfm_playcount > 0 && (
                        <span title="Last.fm plays">
                          {formatPlaycount(artist.lastfm_playcount)}
                        </span>
                      )}

                      {/* Primary genre */}
                      {artist.genres.length > 0 && (
                        <span className="capitalize hidden sm:inline" title="Genre">
                          {artist.genres[0]}
                        </span>
                      )}

                      {/* Popularity - only show on larger screens */}
                      {artist.popularity !== null && artist.popularity > 0 && (
                        <span className="hidden md:inline" title="Spotify Popularity">
                          {artist.popularity}%
                        </span>
                      )}
                    </div>

                    {/* Action button - compact */}
                    {artist.is_manual ? (
                      <button
                        onClick={() => handleRemoveArtist(artist.artist_name)}
                        disabled={isProcessing}
                        className="p-1.5 rounded text-[var(--text-subtle)] hover:text-red-400 hover:bg-red-400/10 transition-colors shrink-0"
                        title={t("removeArtist")}
                      >
                        {isProcessing ? (
                          <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                        ) : (
                          <TrashIcon className="w-4 h-4" />
                        )}
                      </button>
                    ) : isExcluded ? (
                      <button
                        onClick={() => handleIncludeArtist(artist.artist_name)}
                        disabled={isProcessing}
                        className="p-1.5 rounded text-[var(--text-subtle)] hover:text-green-400 hover:bg-green-400/10 transition-colors shrink-0"
                        title={t("unhideFromRecs")}
                      >
                        {isProcessing ? (
                          <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                        ) : (
                          <EyeIcon className="w-4 h-4" />
                        )}
                      </button>
                    ) : (
                      <button
                        onClick={() => handleExcludeArtist(artist.artist_name)}
                        disabled={isProcessing}
                        className="p-1.5 rounded text-[var(--text-subtle)] hover:text-orange-400 hover:bg-orange-400/10 transition-colors shrink-0"
                        title={t("hideFromRecs")}
                      >
                        {isProcessing ? (
                          <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                        ) : (
                          <EyeOffIcon className="w-4 h-4" />
                        )}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Load More */}
          {hasMore && (
            <div className="text-center pt-4">
              <Button
                variant="secondary"
                onClick={() => loadArtists(page + 1, true)}
                isLoading={isLoadingMore}
              >
                {t("loadMoreCount", { current: artists.length, total })}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ============ SONGS TAB ============
function SongsTab({ onCountChange }: { onCountChange: (count: number) => void }) {
  const t = useTranslations("musicIKnow");
  const tCommon = useTranslations("common");
  const [knownSongs, setKnownSongs] = useState<KnownSong[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removingIds, setRemovingIds] = useState<Set<string>>(new Set());

  // Enjoy singing modal state
  const [showEnjoySingingModal, setShowEnjoySingingModal] = useState(false);
  const [selectedSongForModal, setSelectedSongForModal] = useState<KnownSong | null>(null);

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
      setError(err instanceof Error ? err.message : t("failedToLoadSongs"));
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  }, [onCountChange, t]);

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

  const handleEnjoySingingClick = (song: KnownSong) => {
    setSelectedSongForModal(song);
    setShowEnjoySingingModal(true);
  };

  const handleEnjoySingingSuccess = () => {
    // Refresh the song list to show updated enjoy_singing status
    loadKnownSongs(1);
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
            <SearchIcon className="w-4 h-4 text-[var(--text-subtle)]" />
          </div>
          <Input
            type="text"
            placeholder={t("searchSongsToAdd")}
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
                <div className="animate-spin w-5 h-5 border-2 border-[var(--brand-blue)] border-t-transparent rounded-full" />
              </div>
            ) : searchResults.length === 0 ? (
              <p className="text-[var(--text-subtle)] text-sm text-center py-4">
                {t("noSongsFoundFor", { query: searchQuery })}
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {searchResults.map((song) => {
                  const isAdded = addedSongIds.has(song.id);
                  const isAdding = addingIds.has(song.id);
                  return (
                    <div
                      key={song.id}
                      className="flex items-center gap-3 p-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)]"
                    >
                      <div className="flex-1 min-w-0">
                        <h3 className="text-[var(--text)] font-medium truncate">{song.title}</h3>
                        <p className="text-[var(--text-muted)] text-sm truncate">{song.artist}</p>
                      </div>
                      <PopularityStars count={song.brand_count} />
                      <button
                        onClick={() => handleAddSong(song)}
                        disabled={isAdded || isAdding}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-all ${
                          isAdded
                            ? "bg-green-500/20 text-green-400"
                            : isAdding
                            ? "bg-[var(--secondary)] text-[var(--text-subtle)]"
                            : "bg-[var(--brand-pink)]/20 text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/30"
                        }`}
                      >
                        {isAdded ? (
                          <><CheckIcon className="w-4 h-4" /> {tCommon("add")}</>
                        ) : isAdding ? (
                          <><div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" /> {tCommon("add")}</>
                        ) : (
                          <><PlusIcon className="w-4 h-4" /> {tCommon("add")}</>
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
      {searchQuery.trim() && <div className="border-t border-[var(--card-border)]" />}

      {/* Error */}
      {error && (
        <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Known Songs List */}
      {knownSongs.length === 0 && !searchQuery ? (
        <EmptyState
          icon={<MusicIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
          title={t("noSongsYet")}
          description={t("noSongsYetDesc")}
        />
      ) : knownSongs.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-[var(--text-muted)]">{t("mySongs")}</h2>
            <span className="text-[var(--text-subtle)] text-sm">{t("totalSongs", { count: total })}</span>
          </div>

          <div className="flex flex-col gap-2">
            {knownSongs.map((song) => {
              const isRemoving = removingIds.has(song.id);
              return (
                <div
                  key={song.id}
                  className={`flex items-center gap-3 p-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)] transition-opacity ${
                    isRemoving ? "opacity-50" : ""
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-[var(--text)] font-medium truncate">{song.title}</h3>
                      {song.enjoy_singing && (
                        <span className="shrink-0 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-[var(--brand-pink)]/20 text-[var(--brand-pink)]">
                          {t("loveSinging")}
                        </span>
                      )}
                    </div>
                    <p className="text-[var(--text-muted)] text-sm truncate">{song.artist}</p>
                  </div>
                  {/* Enjoy Singing button */}
                  <button
                    onClick={() => handleEnjoySingingClick(song)}
                    className={`p-2 rounded-full transition-colors ${
                      song.enjoy_singing
                        ? "text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/10"
                        : "text-[var(--text-subtle)] hover:text-[var(--brand-pink)] hover:bg-[var(--brand-pink)]/10"
                    }`}
                    title={song.enjoy_singing ? t("editSinging") : t("markAsEnjoy")}
                  >
                    <MicrophoneIcon className="w-4 h-4" />
                  </button>
                  {/* Remove button */}
                  <button
                    onClick={() => handleRemoveSong(song)}
                    disabled={isRemoving}
                    className="p-2 rounded-full text-[var(--text-subtle)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                    title={t("removeSong")}
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
                {tCommon("loadMore")}
              </Button>
            </div>
          )}
        </>
      )}

      {/* Enjoy Singing Modal */}
      {selectedSongForModal && (
        <EnjoySingingModal
          isOpen={showEnjoySingingModal}
          onClose={() => {
            setShowEnjoySingingModal(false);
            setSelectedSongForModal(null);
          }}
          onSuccess={handleEnjoySingingSuccess}
          song={{
            song_id: selectedSongForModal.song_id,
            artist: selectedSongForModal.artist,
            title: selectedSongForModal.title,
            enjoy_singing: selectedSongForModal.enjoy_singing,
            singing_tags: selectedSongForModal.singing_tags as import("@/types").SingingTag[] | undefined,
            singing_energy: selectedSongForModal.singing_energy as import("@/types").SingingEnergy | null | undefined,
            vocal_comfort: selectedSongForModal.vocal_comfort as import("@/types").VocalComfort | null | undefined,
            notes: selectedSongForModal.notes,
          }}
        />
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
  const t = useTranslations("musicIKnow");
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
      setError(err instanceof Error ? err.message : t("failedToLoadServices"));
    } finally {
      setIsLoading(false);
    }
  }, [onCountChange, t]);

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
      setError(err instanceof Error ? err.message : t("failedToConnectSpotify"));
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
      setError(err instanceof Error ? err.message : t("failedToConnectLastfm"));
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
      setError(err instanceof Error ? err.message : t("failedToDisconnect", { service: serviceType }));
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
          setSyncMessage(t("syncComplete", { count: totalMatched }));
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        } else if (response.active_job?.status === "failed") {
          setIsSyncing(false);
          setError(response.active_job.error || "Sync failed");
          if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
        }
      };

      pollIntervalRef.current = setInterval(checkStatus, 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("failedToStartSync"));
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
        <div className="w-16 h-16 rounded-full bg-[var(--card)] flex items-center justify-center mx-auto mb-4">
          <SpotifyIcon className="w-8 h-8 text-[var(--text-subtle)]" />
        </div>
        <h3 className="text-lg font-medium text-[var(--text)] mb-2">{t("connectYourServices")}</h3>
        <p className="text-[var(--text-muted)] text-sm mb-6 max-w-sm mx-auto">
          {t("connectServicesGuestDesc")}
        </p>
        <Link href="/login">
          <Button variant="primary">{t("createAccount")}</Button>
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
      <div className="p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-full bg-[#1DB954]/20 flex items-center justify-center">
            <SpotifyIcon className="w-5 h-5 text-[#1DB954]" />
          </div>
          <div className="flex-1">
            <h3 className="font-medium text-[var(--text)]">{t("spotify")}</h3>
            {isConnected("spotify") && (
              <p className="text-xs text-[var(--text-muted)]">{getService("spotify")?.service_username}</p>
            )}
          </div>
          {isConnected("spotify") ? (
            <Badge variant="success">{t("connected")}</Badge>
          ) : (
            <Badge variant="default">{t("notConnected")}</Badge>
          )}
        </div>

        {isConnected("spotify") ? (
          <div className="space-y-2">
            <div className="flex items-center gap-4 text-xs text-[var(--text-subtle)]">
              <span>{t("tracks", { count: getService("spotify")?.tracks_synced || 0 })}</span>
              {getService("spotify")?.last_sync_at && (
                <span>{t("lastSync", { date: formatDate(getService("spotify")!.last_sync_at!) })}</span>
              )}
            </div>
            <Button variant="danger" size="sm" onClick={() => handleDisconnect("spotify")} isLoading={disconnecting === "spotify"}>
              {t("disconnect")}
            </Button>
          </div>
        ) : (
          <Button variant="primary" size="sm" onClick={handleConnectSpotify} leftIcon={<SpotifyIcon className="w-4 h-4" />}>
            {t("connectSpotify")}
          </Button>
        )}
      </div>

      {/* Last.fm */}
      <div className="p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-full bg-[#D51007]/20 flex items-center justify-center">
            <LastfmIcon className="w-5 h-5 text-[#ff4444]" />
          </div>
          <div className="flex-1">
            <h3 className="font-medium text-[var(--text)]">{t("lastfm")}</h3>
            {isConnected("lastfm") && (
              <p className="text-xs text-[var(--text-muted)]">{getService("lastfm")?.service_username}</p>
            )}
          </div>
          {isConnected("lastfm") ? (
            <Badge variant="success">{t("connected")}</Badge>
          ) : (
            <Badge variant="default">{t("notConnected")}</Badge>
          )}
        </div>

        {isConnected("lastfm") ? (
          <div className="space-y-2">
            <div className="flex items-center gap-4 text-xs text-[var(--text-subtle)]">
              <span>{t("tracks", { count: getService("lastfm")?.tracks_synced || 0 })}</span>
              {getService("lastfm")?.last_sync_at && (
                <span>{t("lastSync", { date: formatDate(getService("lastfm")!.last_sync_at!) })}</span>
              )}
            </div>
            <Button variant="danger" size="sm" onClick={() => handleDisconnect("lastfm")} isLoading={disconnecting === "lastfm"}>
              {t("disconnect")}
            </Button>
          </div>
        ) : (
          <form onSubmit={handleConnectLastfm} className="space-y-2">
            <Input
              placeholder={t("lastfmPlaceholder")}
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
              {t("connectLastfm")}
            </Button>
          </form>
        )}
      </div>

      {/* Sync Button */}
      {services.length > 0 && (
        <div className="p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-medium text-[var(--text)] text-sm">{t("syncListeningHistory")}</h3>
              <p className="text-xs text-[var(--text-subtle)]">
                {isSyncing ? t("syncingInBackground") : t("fetchLatest")}
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
              {isSyncing ? t("syncing") : t("syncNow")}
            </Button>
          </div>
        </div>
      )}

      {/* Tip for Spotify-only users */}
      {isConnected("spotify") && !isConnected("lastfm") && (
        <div className="p-3 rounded-xl bg-[#ff4444]/10 border border-[#ff4444]/20">
          <p className="text-xs text-[var(--text-muted)]">
            <strong className="text-[#ff4444]">{t("tip")}</strong> {t("lastfmTip")}
          </p>
        </div>
      )}
    </div>
  );
}
