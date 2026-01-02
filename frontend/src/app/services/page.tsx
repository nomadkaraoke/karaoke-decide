"use client";

import { useState, useEffect, useCallback, FormEvent, useRef } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { ProtectedPage } from "@/components/ProtectedPage";
import { UpgradePrompt } from "@/components/UpgradePrompt";
import {
  LinkIcon,
  SpotifyIcon,
  LastfmIcon,
  RefreshIcon,
  CheckIcon,
  XIcon,
} from "@/components/icons";
import { Button, Input, Badge, LoadingPulse } from "@/components/ui";

interface ConnectedService {
  service_type: string;
  service_username: string;
  last_sync_at: string | null;
  sync_status: string;
  sync_error: string | null;
  tracks_synced: number;
}

interface SyncProgress {
  current_service: string | null;
  current_phase: string | null;
  total_tracks: number;
  processed_tracks: number;
  matched_tracks: number;
  percentage: number;
}

interface ActiveJob {
  job_id: string;
  status: string;
  progress: SyncProgress | null;
  results: Array<{
    service_type: string;
    tracks_fetched: number;
    tracks_matched: number;
    user_songs_created: number;
    user_songs_updated: number;
    artists_stored: number;
    error: string | null;
  }> | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

interface UserArtist {
  id: string;
  artist_name: string;
  source: string;
  rank: number;
  time_range: string;
  popularity: number | null;
  genres: string[];
}

export default function ServicesPage() {
  const { isGuest } = useAuth();
  const [services, setServices] = useState<ConnectedService[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Last.fm form state
  const [lastfmUsername, setLastfmUsername] = useState("");
  const [isConnectingLastfm, setIsConnectingLastfm] = useState(false);
  const [lastfmError, setLastfmError] = useState<string | null>(null);

  // Sync state
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<ActiveJob | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Disconnect state
  const [disconnecting, setDisconnecting] = useState<string | null>(null);

  // Artists state
  const [artists, setArtists] = useState<UserArtist[]>([]);
  const [artistSources, setArtistSources] = useState<Record<string, number>>({});
  const [showArtists, setShowArtists] = useState(false);
  const [artistsLoading, setArtistsLoading] = useState(false);

  // Load artists from API
  const loadArtists = useCallback(async () => {
    try {
      setArtistsLoading(true);
      const response = await api.my.getArtists(undefined, undefined, 200);
      setArtists(response.artists);
      setArtistSources(response.sources);
    } catch (err) {
      console.error("Failed to load artists:", err);
    } finally {
      setArtistsLoading(false);
    }
  }, []);

  // Polling for sync status (defined before loadServices since it's used there)
  const pollSyncStatus = useCallback(async () => {
    try {
      const response = await api.services.getSyncStatus();
      setServices(response.services);
      setActiveJob(response.active_job);

      if (response.active_job) {
        if (response.active_job.status === "completed") {
          // Sync completed
          setIsSyncing(false);
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }

          // Show success message
          const totalMatched = response.active_job.results?.reduce(
            (sum, r) => sum + r.tracks_matched, 0
          ) || 0;
          const totalCreated = response.active_job.results?.reduce(
            (sum, r) => sum + r.user_songs_created, 0
          ) || 0;
          const totalArtists = response.active_job.results?.reduce(
            (sum, r) => sum + r.artists_stored, 0
          ) || 0;
          setSyncMessage(
            `Sync complete! Found ${totalMatched} karaoke songs, added ${totalCreated} new songs to your library.${totalArtists > 0 ? ` Synced ${totalArtists} artists.` : ""}`
          );

          // Refresh artists list
          loadArtists();
        } else if (response.active_job.status === "failed") {
          // Sync failed
          setIsSyncing(false);
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          setError(response.active_job.error || "Sync failed");
        }
      }
    } catch (err) {
      // Don't show error for polling failures
      console.error("Polling error:", err);
    }
  }, [loadArtists]);

  const loadServices = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.services.getSyncStatus();
      setServices(response.services);
      setActiveJob(response.active_job);

      // If there's an active job, start polling
      if (response.active_job &&
          (response.active_job.status === "pending" || response.active_job.status === "in_progress")) {
        setIsSyncing(true);
        // Start polling if not already polling
        if (!pollIntervalRef.current) {
          pollIntervalRef.current = setInterval(pollSyncStatus, 3000);
        }
      } else {
        setIsSyncing(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load services");
    } finally {
      setIsLoading(false);
    }
  }, [pollSyncStatus]);

  useEffect(() => {
    loadServices();
    loadArtists();

    // Cleanup polling on unmount
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [loadServices, loadArtists]);

  const isConnected = (serviceType: string) =>
    services.some((s) => s.service_type === serviceType);

  const getService = (serviceType: string) =>
    services.find((s) => s.service_type === serviceType);

  const handleConnectSpotify = async () => {
    try {
      const response = await api.services.connectSpotify();
      // Redirect to Spotify OAuth
      window.location.href = response.auth_url;
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to connect Spotify"
      );
    }
  };

  const handleConnectLastfm = async (e: FormEvent) => {
    e.preventDefault();
    if (!lastfmUsername.trim()) return;

    try {
      setIsConnectingLastfm(true);
      setLastfmError(null);
      await api.services.connectLastfm(lastfmUsername);
      setLastfmUsername("");
      await loadServices();
    } catch (err) {
      setLastfmError(
        err instanceof Error ? err.message : "Failed to connect Last.fm"
      );
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
      setError(
        err instanceof Error ? err.message : `Failed to disconnect ${serviceType}`
      );
    } finally {
      setDisconnecting(null);
    }
  };

  const handleSync = async () => {
    try {
      setIsSyncing(true);
      setSyncMessage(null);
      setError(null);

      // Start async sync
      await api.services.sync();

      // Start polling for status
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
      pollIntervalRef.current = setInterval(pollSyncStatus, 3000);

      // Do an immediate poll
      await pollSyncStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start sync");
      setIsSyncing(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  // Show upgrade prompt for guest users
  if (isGuest) {
    return (
      <ProtectedPage>
        <main className="min-h-screen pb-safe">
          <div className="max-w-2xl mx-auto px-4 py-12">
            <UpgradePrompt
              title="Connect Your Music Services"
              description="Create an account to connect Spotify, Last.fm, and sync your listening history."
              featureName="Music Services"
            />
          </div>
        </main>
      </ProtectedPage>
    );
  }

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-white flex items-center gap-3">
              <LinkIcon className="w-7 h-7 text-[#00f5ff]" />
              Music Services
            </h1>
            <p className="text-white/60 text-sm mt-1">
              Connect your streaming services to sync your listening history
            </p>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400">
              {error}
            </div>
          )}

          {/* Sync message */}
          {syncMessage && (
            <div className="mb-6 p-4 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400">
              {syncMessage}
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={2} />
          ) : (
            <div className="space-y-6">
              {/* Spotify */}
              <div className="p-5 rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10">
                <div className="flex items-center gap-4 mb-4">
                  <div className="w-12 h-12 rounded-full bg-[#1DB954]/20 flex items-center justify-center">
                    <SpotifyIcon className="w-6 h-6 text-[#1DB954]" />
                  </div>
                  <div className="flex-1">
                    <h2 className="text-lg font-semibold text-white">Spotify</h2>
                    {isConnected("spotify") ? (
                      <p className="text-sm text-white/60">
                        Connected as{" "}
                        <span className="text-white">
                          {getService("spotify")?.service_username}
                        </span>
                      </p>
                    ) : (
                      <p className="text-sm text-white/60">Not connected</p>
                    )}
                  </div>
                  {isConnected("spotify") ? (
                    <Badge variant="success">Connected</Badge>
                  ) : (
                    <Badge variant="default">Not connected</Badge>
                  )}
                </div>

                {isConnected("spotify") ? (
                  <div className="space-y-3">
                    {/* Stats */}
                    <div className="flex items-center gap-4 text-sm text-white/60">
                      <span>
                        {getService("spotify")?.tracks_synced || 0} tracks synced
                      </span>
                      {getService("spotify")?.last_sync_at && (
                        <span>
                          Last sync:{" "}
                          {formatDate(getService("spotify")!.last_sync_at!)}
                        </span>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex gap-2">
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDisconnect("spotify")}
                        isLoading={disconnecting === "spotify"}
                      >
                        Disconnect
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Button
                    variant="primary"
                    onClick={handleConnectSpotify}
                    leftIcon={<SpotifyIcon className="w-5 h-5" />}
                  >
                    Connect Spotify
                  </Button>
                )}
              </div>

              {/* Last.fm */}
              <div className="p-5 rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10">
                <div className="flex items-center gap-4 mb-4">
                  <div className="w-12 h-12 rounded-full bg-[#D51007]/20 flex items-center justify-center">
                    <LastfmIcon className="w-6 h-6 text-[#ff4444]" />
                  </div>
                  <div className="flex-1">
                    <h2 className="text-lg font-semibold text-white">Last.fm</h2>
                    {isConnected("lastfm") ? (
                      <p className="text-sm text-white/60">
                        Connected as{" "}
                        <span className="text-white">
                          {getService("lastfm")?.service_username}
                        </span>
                      </p>
                    ) : (
                      <p className="text-sm text-white/60">Not connected</p>
                    )}
                  </div>
                  {isConnected("lastfm") ? (
                    <Badge variant="success">Connected</Badge>
                  ) : (
                    <Badge variant="default">Not connected</Badge>
                  )}
                </div>

                {isConnected("lastfm") ? (
                  <div className="space-y-3">
                    {/* Stats */}
                    <div className="flex items-center gap-4 text-sm text-white/60">
                      <span>
                        {getService("lastfm")?.tracks_synced || 0} tracks synced
                      </span>
                      {getService("lastfm")?.last_sync_at && (
                        <span>
                          Last sync:{" "}
                          {formatDate(getService("lastfm")!.last_sync_at!)}
                        </span>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex gap-2">
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => handleDisconnect("lastfm")}
                        isLoading={disconnecting === "lastfm"}
                      >
                        Disconnect
                      </Button>
                    </div>
                  </div>
                ) : (
                  <form onSubmit={handleConnectLastfm} className="space-y-3">
                    <Input
                      placeholder="Your Last.fm username"
                      value={lastfmUsername}
                      onChange={(e) => setLastfmUsername(e.target.value)}
                      error={lastfmError || undefined}
                    />
                    <Button
                      type="submit"
                      variant="primary"
                      isLoading={isConnectingLastfm}
                      disabled={!lastfmUsername.trim()}
                      leftIcon={<LastfmIcon className="w-5 h-5" />}
                    >
                      Connect Last.fm
                    </Button>
                  </form>
                )}
              </div>

              {/* Sync button and progress */}
              {services.length > 0 && (
                <div className="p-5 rounded-2xl bg-white/5 border border-white/10">
                  <div className="flex items-center justify-between mb-4">
                    <div>
                      <h3 className="font-semibold text-white">
                        Sync listening history
                      </h3>
                      <p className="text-sm text-white/60">
                        {isSyncing
                          ? "Syncing in background - you can navigate away safely"
                          : "Fetch your latest tracks from connected services"}
                      </p>
                    </div>
                    <Button
                      variant="secondary"
                      onClick={handleSync}
                      isLoading={isSyncing}
                      disabled={isSyncing}
                      leftIcon={<RefreshIcon className="w-5 h-5" />}
                    >
                      {isSyncing ? "Syncing..." : "Sync Now"}
                    </Button>
                  </div>

                  {/* Progress display */}
                  {isSyncing && activeJob?.progress && (
                    <div className="mt-4 space-y-3">
                      {/* Progress bar */}
                      <div className="relative h-2 bg-white/10 rounded-full overflow-hidden">
                        <div
                          className="absolute inset-y-0 left-0 bg-[#00f5ff] transition-all duration-500"
                          style={{ width: `${activeJob.progress.percentage}%` }}
                        />
                      </div>

                      {/* Progress details */}
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-white/60">
                          {activeJob.progress.current_service && (
                            <>
                              <span className="capitalize">{activeJob.progress.current_service}</span>
                              {activeJob.progress.current_phase && (
                                <span className="text-white/40">
                                  {" "}
                                  - {activeJob.progress.current_phase}
                                </span>
                              )}
                            </>
                          )}
                        </span>
                        <span className="text-white font-medium">
                          {activeJob.progress.percentage}%
                        </span>
                      </div>

                      {/* Track counts */}
                      {activeJob.progress.total_tracks > 0 && (
                        <div className="flex gap-4 text-xs text-white/50">
                          <span>
                            {activeJob.progress.processed_tracks} / {activeJob.progress.total_tracks} tracks processed
                          </span>
                          <span>
                            {activeJob.progress.matched_tracks} matched to karaoke
                          </span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Pending state */}
                  {isSyncing && activeJob?.status === "pending" && (
                    <div className="mt-4 text-sm text-white/60 animate-pulse">
                      Starting sync...
                    </div>
                  )}
                </div>
              )}

              {/* Fetched Artists */}
              {artists.length > 0 && (
                <div className="p-5 rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10">
                  <button
                    onClick={() => setShowArtists(!showArtists)}
                    className="w-full flex items-center justify-between text-left"
                  >
                    <div>
                      <h3 className="font-semibold text-white">
                        Your Top Artists
                      </h3>
                      <p className="text-sm text-white/60">
                        {artists.length} artists from{" "}
                        {Object.entries(artistSources)
                          .map(([src, count]) => `${src} (${count})`)
                          .join(", ")}
                      </p>
                    </div>
                    <svg
                      className={`w-5 h-5 text-white/60 transition-transform ${showArtists ? "rotate-180" : ""}`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>

                  {showArtists && (
                    <div className="mt-4">
                      {artistsLoading ? (
                        <LoadingPulse count={3} />
                      ) : (
                        <div className="space-y-4">
                          {/* Group by source */}
                          {["spotify", "lastfm"].map((source) => {
                            const sourceArtists = artists.filter((a) => a.source === source);
                            if (sourceArtists.length === 0) return null;

                            // Group by time_range
                            const byTimeRange = sourceArtists.reduce((acc, artist) => {
                              const tr = artist.time_range || "unknown";
                              if (!acc[tr]) acc[tr] = [];
                              acc[tr].push(artist);
                              return acc;
                            }, {} as Record<string, UserArtist[]>);

                            return (
                              <div key={source}>
                                <h4 className="text-sm font-medium text-white/80 mb-2 flex items-center gap-2">
                                  {source === "spotify" ? (
                                    <SpotifyIcon className="w-4 h-4 text-[#1DB954]" />
                                  ) : (
                                    <LastfmIcon className="w-4 h-4 text-[#ff4444]" />
                                  )}
                                  <span className="capitalize">{source}</span>
                                </h4>
                                {Object.entries(byTimeRange).map(([timeRange, rangeArtists]) => (
                                  <div key={timeRange} className="mb-3">
                                    <p className="text-xs text-white/50 mb-1">
                                      {timeRange.replace("_", " ")}
                                    </p>
                                    <div className="flex flex-wrap gap-2">
                                      {rangeArtists.slice(0, 20).map((artist) => (
                                        <span
                                          key={artist.id}
                                          className="px-2 py-1 text-xs rounded-full bg-white/10 text-white/80 hover:bg-white/20 transition-colors"
                                          title={artist.genres.length > 0 ? artist.genres.join(", ") : undefined}
                                        >
                                          {artist.artist_name}
                                          {artist.rank && artist.rank <= 10 && (
                                            <span className="ml-1 text-[#00f5ff]">#{artist.rank}</span>
                                          )}
                                        </span>
                                      ))}
                                      {rangeArtists.length > 20 && (
                                        <span className="px-2 py-1 text-xs text-white/50">
                                          +{rangeArtists.length - 20} more
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </ProtectedPage>
  );
}
