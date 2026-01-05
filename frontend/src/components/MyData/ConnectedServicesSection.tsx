"use client";

import { useState, useCallback, useRef, useEffect, FormEvent } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import {
  SpotifyIcon,
  LastfmIcon,
  RefreshIcon,
  ChevronDownIcon,
} from "@/components/icons";
import { Button, Input, Badge, LoadingPulse } from "@/components/ui";
import { UpgradePrompt } from "@/components/UpgradePrompt";

interface ConnectedService {
  service_type: string;
  service_username: string;
  last_sync_at: string | null;
  sync_status: string;
  sync_error: string | null;
  tracks_synced: number;  // Karaoke-matched tracks
  songs_synced: number;   // Total unique songs synced
  artists_synced?: number;
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

interface Props {
  isExpanded: boolean;
  onToggle: () => void;
  onSyncComplete?: () => void;
}

export function ConnectedServicesSection({
  isExpanded,
  onToggle,
  onSyncComplete,
}: Props) {
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

  // Polling for sync status
  const pollSyncStatus = useCallback(async () => {
    try {
      const response = await api.services.getSyncStatus();
      setServices(response.services);
      setActiveJob(response.active_job);

      if (response.active_job) {
        if (response.active_job.status === "completed") {
          setIsSyncing(false);
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }

          const totalMatched =
            response.active_job.results?.reduce(
              (sum, r) => sum + r.tracks_matched,
              0
            ) || 0;
          const totalCreated =
            response.active_job.results?.reduce(
              (sum, r) => sum + r.user_songs_created,
              0
            ) || 0;
          const totalArtists =
            response.active_job.results?.reduce(
              (sum, r) => sum + r.artists_stored,
              0
            ) || 0;
          setSyncMessage(
            `Sync complete! Found ${totalMatched} karaoke songs, added ${totalCreated} new songs.${totalArtists > 0 ? ` Synced ${totalArtists} artists.` : ""}`
          );

          onSyncComplete?.();
        } else if (response.active_job.status === "failed") {
          setIsSyncing(false);
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
          setError(response.active_job.error || "Sync failed");
        }
      }
    } catch (err) {
      console.error("Polling error:", err);
    }
  }, [onSyncComplete]);

  const loadServices = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.services.getSyncStatus();
      setServices(response.services);
      setActiveJob(response.active_job);

      if (
        response.active_job &&
        (response.active_job.status === "pending" ||
          response.active_job.status === "in_progress")
      ) {
        setIsSyncing(true);
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
    if (!isGuest) {
      loadServices();
    } else {
      setIsLoading(false);
    }

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [loadServices, isGuest]);

  const isConnected = (serviceType: string) =>
    services.some((s) => s.service_type === serviceType);

  const getService = (serviceType: string) =>
    services.find((s) => s.service_type === serviceType);

  const handleConnectSpotify = async () => {
    try {
      const response = await api.services.connectSpotify();
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
        err instanceof Error
          ? err.message
          : `Failed to disconnect ${serviceType}`
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

      await api.services.sync();

      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
      pollIntervalRef.current = setInterval(pollSyncStatus, 3000);

      await pollSyncStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start sync");
      setIsSyncing(false);
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const connectedCount = services.length;

  // Show upgrade prompt for guests
  if (isGuest) {
    return (
      <div className="rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 overflow-hidden">
        <button
          onClick={onToggle}
          aria-expanded={isExpanded}
          className="w-full p-5 flex items-center justify-between text-left"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-[#00f5ff]/20 flex items-center justify-center">
              <SpotifyIcon className="w-5 h-5 text-[#00f5ff]" />
            </div>
            <div>
              <h2 className="font-semibold text-white">Connected Services</h2>
              <p className="text-sm text-white/60">
                Create an account to connect
              </p>
            </div>
          </div>
          <ChevronDownIcon
            className={`w-5 h-5 text-white/60 transition-transform ${isExpanded ? "rotate-180" : ""}`}
          />
        </button>

        {isExpanded && (
          <div className="px-5 pb-5">
            <UpgradePrompt
              title="Connect Your Music Services"
              description="Create an account to connect Spotify and Last.fm for personalized recommendations."
              featureName="Music Services"
            />
          </div>
        )}
      </div>
    );
  }

  const spotifyConnected = isConnected("spotify");
  const lastfmConnected = isConnected("lastfm");

  return (
    <div className="rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 overflow-hidden">
      {/* Header - shows service logos when collapsed */}
      <button
        onClick={onToggle}
        aria-expanded={isExpanded}
        className="w-full p-5 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          {/* Show connected service logos or generic icon */}
          {connectedCount > 0 ? (
            <div className="flex items-center gap-1">
              {spotifyConnected && (
                <div className="w-9 h-9 rounded-full bg-[#1DB954]/20 flex items-center justify-center">
                  <SpotifyIcon className="w-4 h-4 text-[#1DB954]" />
                </div>
              )}
              {lastfmConnected && (
                <div className="w-9 h-9 rounded-full bg-[#D51007]/20 flex items-center justify-center">
                  <LastfmIcon className="w-4 h-4 text-[#ff4444]" />
                </div>
              )}
            </div>
          ) : (
            <div className="w-10 h-10 rounded-full bg-white/10 flex items-center justify-center">
              <SpotifyIcon className="w-5 h-5 text-white/40" />
            </div>
          )}
          <div>
            <h2 className="font-semibold text-white">Connected Services</h2>
            <p className="text-sm text-white/60">
              {connectedCount === 0
                ? "No services connected"
                : [
                    spotifyConnected && "Spotify",
                    lastfmConnected && "Last.fm",
                  ]
                    .filter(Boolean)
                    .join(", ")}
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
            </div>
          )}

          {/* Sync message */}
          {syncMessage && (
            <div className="p-3 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400 text-sm">
              {syncMessage}
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={2} />
          ) : (
            <>
              {/* Spotify */}
              <div className="p-4 rounded-xl bg-white/5 border border-white/10">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-8 h-8 rounded-full bg-[#1DB954]/20 flex items-center justify-center">
                    <SpotifyIcon className="w-4 h-4 text-[#1DB954]" />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-medium text-white">Spotify</h3>
                    {isConnected("spotify") ? (
                      <p className="text-xs text-white/60">
                        {getService("spotify")?.service_username}
                      </p>
                    ) : null}
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
                      <span>
                        {getService("spotify")?.songs_synced || getService("spotify")?.tracks_synced || 0} songs
                      </span>
                      <span>
                        {getService("spotify")?.artists_synced || 0} artists
                      </span>
                      {getService("spotify")?.last_sync_at && (
                        <span>
                          Last sync:{" "}
                          {formatDate(getService("spotify")!.last_sync_at!)}
                        </span>
                      )}
                    </div>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => handleDisconnect("spotify")}
                      isLoading={disconnecting === "spotify"}
                    >
                      Disconnect
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleConnectSpotify}
                    leftIcon={<SpotifyIcon className="w-4 h-4" />}
                  >
                    Connect Spotify
                  </Button>
                )}
              </div>

              {/* Last.fm */}
              <div className="p-4 rounded-xl bg-white/5 border border-white/10">
                <div className="flex items-center gap-3 mb-3">
                  <div className="w-8 h-8 rounded-full bg-[#D51007]/20 flex items-center justify-center">
                    <LastfmIcon className="w-4 h-4 text-[#ff4444]" />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-medium text-white">Last.fm</h3>
                    {isConnected("lastfm") ? (
                      <p className="text-xs text-white/60">
                        {getService("lastfm")?.service_username}
                      </p>
                    ) : null}
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
                      <span>
                        {getService("lastfm")?.songs_synced || getService("lastfm")?.tracks_synced || 0} songs
                      </span>
                      <span>
                        {getService("lastfm")?.artists_synced || 0} artists
                      </span>
                      {getService("lastfm")?.last_sync_at && (
                        <span>
                          Last sync:{" "}
                          {formatDate(getService("lastfm")!.last_sync_at!)}
                        </span>
                      )}
                    </div>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => handleDisconnect("lastfm")}
                      isLoading={disconnecting === "lastfm"}
                    >
                      Disconnect
                    </Button>
                  </div>
                ) : (
                  <form onSubmit={handleConnectLastfm} className="space-y-2">
                    <Input
                      placeholder="Your Last.fm username"
                      value={lastfmUsername}
                      onChange={(e) => setLastfmUsername(e.target.value)}
                      error={lastfmError || undefined}
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

              {/* Sync button */}
              {services.length > 0 && (
                <div className="p-4 rounded-xl bg-white/5 border border-white/10">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-medium text-white text-sm">
                        Sync listening history
                      </h3>
                      <p className="text-xs text-white/50">
                        {isSyncing
                          ? "Syncing in background..."
                          : "Fetch latest from services"}
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

                  {/* Progress display */}
                  {isSyncing && activeJob?.progress && (
                    <div className="mt-3 space-y-2">
                      <div className="relative h-1.5 bg-white/10 rounded-full overflow-hidden">
                        <div
                          className="absolute inset-y-0 left-0 bg-[#00f5ff] transition-all duration-500"
                          style={{ width: `${activeJob.progress.percentage}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-white/50">
                          {activeJob.progress.current_service && (
                            <>
                              <span className="capitalize">
                                {activeJob.progress.current_service}
                              </span>
                              {activeJob.progress.current_phase && (
                                <span className="text-white/30">
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
                    </div>
                  )}
                </div>
              )}

              {/* Last.fm encouragement for Spotify-only users */}
              {isConnected("spotify") && !isConnected("lastfm") && (
                <div className="p-3 rounded-xl bg-[#ff4444]/10 border border-[#ff4444]/20">
                  <p className="text-xs text-white/70">
                    <strong className="text-[#ff4444]">Tip:</strong> Connect
                    Last.fm for more accurate recommendations based on your full
                    listening history.
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
