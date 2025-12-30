"use client";

import { useState, useEffect, useCallback, FormEvent } from "react";
import { api } from "@/lib/api";
import { ProtectedPage } from "@/components/ProtectedPage";
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

export default function ServicesPage() {
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

  // Disconnect state
  const [disconnecting, setDisconnecting] = useState<string | null>(null);

  const loadServices = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.services.list();
      setServices(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load services");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadServices();
  }, [loadServices]);

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
      const response = await api.services.sync();

      // Summarize results
      const totalMatched = response.results.reduce(
        (sum, r) => sum + r.tracks_matched,
        0
      );
      const totalCreated = response.results.reduce(
        (sum, r) => sum + r.user_songs_created,
        0
      );
      setSyncMessage(
        `Synced! Found ${totalMatched} matching songs, added ${totalCreated} new songs.`
      );

      await loadServices();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sync failed");
    } finally {
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

              {/* Sync button */}
              {services.length > 0 && (
                <div className="p-5 rounded-2xl bg-white/5 border border-white/10">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold text-white">
                        Sync listening history
                      </h3>
                      <p className="text-sm text-white/60">
                        Fetch your latest tracks from connected services
                      </p>
                    </div>
                    <Button
                      variant="secondary"
                      onClick={handleSync}
                      isLoading={isSyncing}
                      leftIcon={<RefreshIcon className="w-5 h-5" />}
                    >
                      Sync Now
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </ProtectedPage>
  );
}
