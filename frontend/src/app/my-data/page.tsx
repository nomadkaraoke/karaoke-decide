"use client";

import { useState, useCallback, useEffect } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { ProtectedPage } from "@/components/ProtectedPage";
import {
  ConnectedServicesSection,
  YourArtistsSection,
  YourSongsSection,
  PreferencesSection,
} from "@/components/MyData";
import { DatabaseIcon, SparklesIcon } from "@/components/icons";
import { Button, LoadingPulse } from "@/components/ui";

interface DataSummary {
  services: Record<
    string,
    {
      connected: boolean;
      username?: string;
      tracks_synced?: number;
      last_sync_at?: string;
    }
  >;
  artists: {
    total: number;
    by_source: Record<string, number>;
  };
  songs: {
    total: number;
    with_karaoke: number;
  };
  preferences: {
    completed: boolean;
    decade?: string;
    energy?: string;
    genres?: string[];
  };
}

export default function MyDataPage() {
  const { isGuest } = useAuth();
  const [summary, setSummary] = useState<DataSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Expanded sections
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(["services"])
  );

  // Refresh trigger for child components
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  const loadSummary = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.my.getDataSummary();
      setSummary(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data summary");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  const toggleSection = (section: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  };

  const handleSyncComplete = () => {
    // Refresh data after sync completes
    loadSummary();
    setRefreshTrigger((prev) => prev + 1);
  };

  // Calculate summary stats
  const connectedServices = summary
    ? Object.values(summary.services).filter((s) => s.connected).length
    : 0;
  const totalArtists = summary?.artists.total || 0;
  const totalSongs = summary?.songs.total || 0;

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                <DatabaseIcon className="w-7 h-7 text-[#00f5ff]" />
                My Data
              </h1>
              <p className="text-white/60 text-sm mt-1">
                Everything we know about your music taste
              </p>
            </div>
            <Link href="/recommendations">
              <Button variant="secondary" size="sm">
                <SparklesIcon className="w-4 h-4" />
                Recommendations
              </Button>
            </Link>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400">
              {error}
              <button
                onClick={loadSummary}
                className="ml-2 underline hover:no-underline"
              >
                Retry
              </button>
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={4} />
          ) : (
            <>
              {/* Summary stats */}
              {summary && (
                <div className="grid grid-cols-3 gap-3 mb-6">
                  <div className="p-4 rounded-xl bg-white/5 border border-white/10 text-center">
                    <p className="text-2xl font-bold text-white">
                      {connectedServices}
                    </p>
                    <p className="text-xs text-white/50">
                      Service{connectedServices !== 1 ? "s" : ""}
                    </p>
                  </div>
                  <div className="p-4 rounded-xl bg-white/5 border border-white/10 text-center">
                    <p className="text-2xl font-bold text-white">{totalArtists}</p>
                    <p className="text-xs text-white/50">
                      Artist{totalArtists !== 1 ? "s" : ""}
                    </p>
                  </div>
                  <div className="p-4 rounded-xl bg-white/5 border border-white/10 text-center">
                    <p className="text-2xl font-bold text-white">{totalSongs}</p>
                    <p className="text-xs text-white/50">
                      Song{totalSongs !== 1 ? "s" : ""}
                    </p>
                  </div>
                </div>
              )}

              {/* Sections */}
              <div className="space-y-4">
                {/* Connected Services */}
                <ConnectedServicesSection
                  isExpanded={expandedSections.has("services")}
                  onToggle={() => toggleSection("services")}
                  onSyncComplete={handleSyncComplete}
                />

                {/* Your Artists */}
                <YourArtistsSection
                  isExpanded={expandedSections.has("artists")}
                  onToggle={() => toggleSection("artists")}
                  refreshTrigger={refreshTrigger}
                />

                {/* Your Songs */}
                <YourSongsSection
                  isExpanded={expandedSections.has("songs")}
                  onToggle={() => toggleSection("songs")}
                  refreshTrigger={refreshTrigger}
                />

                {/* Preferences */}
                <PreferencesSection
                  isExpanded={expandedSections.has("preferences")}
                  onToggle={() => toggleSection("preferences")}
                />

                {/* Feedback section (future placeholder) */}
                <div className="rounded-2xl bg-white/5 border border-white/10 p-5">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-white/10 flex items-center justify-center">
                      <span className="text-lg text-white/40">*</span>
                    </div>
                    <div>
                      <h2 className="font-semibold text-white/40">
                        Feedback & Refinements
                      </h2>
                      <p className="text-sm text-white/30">
                        Coming soon: Love/hide songs, vocal range, more!
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Footer help text */}
              <div className="mt-8 text-center text-sm text-white/40">
                <p>
                  This data powers your personalized karaoke recommendations.
                </p>
                <p className="mt-1">
                  {isGuest ? (
                    <>
                      <Link href="/login" className="text-[#00f5ff] hover:underline">
                        Create an account
                      </Link>{" "}
                      to sync your music services.
                    </>
                  ) : (
                    <>
                      More data = better recommendations.{" "}
                      <Link href="/quiz" className="text-[#00f5ff] hover:underline">
                        Take the quiz
                      </Link>{" "}
                      or connect more services.
                    </>
                  )}
                </p>
              </div>
            </>
          )}
        </div>
      </main>
    </ProtectedPage>
  );
}
