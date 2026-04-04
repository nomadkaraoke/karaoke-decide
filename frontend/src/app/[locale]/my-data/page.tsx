"use client";

import { useState, useCallback, useEffect } from "react";
import { Link } from "@/i18n/routing";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("myData");
  const [summary, setSummary] = useState<DataSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Expanded sections - preferences expanded by default, services collapsed
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(["preferences"])
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
      setError(err instanceof Error ? err.message : t("failedToLoadSummary"));
    } finally {
      setIsLoading(false);
    }
  }, [t]);

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
              <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
                <DatabaseIcon className="w-7 h-7 text-[var(--brand-blue)]" />
                {t("title")}
              </h1>
              <p className="text-[var(--text-muted)] text-sm mt-1">
                {t("subtitle")}
              </p>
            </div>
            <Link href="/recommendations">
              <Button variant="secondary" size="sm">
                <SparklesIcon className="w-4 h-4" />
                {t("recommendations")}
              </Button>
            </Link>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400">
              {error}
              <button
                onClick={loadSummary}
                className="ms-2 underline hover:no-underline"
              >
                {t("retry")}
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
                  <div className="p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)] text-center">
                    <p className="text-2xl font-bold text-[var(--text)]">
                      {connectedServices}
                    </p>
                    <p className="text-xs text-[var(--text-subtle)]">
                      {connectedServices !== 1 ? t("servicesPlural") : t("servicesSingular")}
                    </p>
                  </div>
                  <div className="p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)] text-center">
                    <p className="text-2xl font-bold text-[var(--text)]">{totalArtists}</p>
                    <p className="text-xs text-[var(--text-subtle)]">
                      {totalArtists !== 1 ? t("artistsPlural") : t("artistsSingular")}
                    </p>
                  </div>
                  <div className="p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)] text-center">
                    <p className="text-2xl font-bold text-[var(--text)]">{totalSongs}</p>
                    <p className="text-xs text-[var(--text-subtle)]">
                      {totalSongs !== 1 ? t("songsPlural") : t("songsSingular")}
                    </p>
                  </div>
                </div>
              )}

              {/* Sections - Reordered with Preferences first */}
              <div className="space-y-4">
                {/* Preferences (most actionable, first) */}
                <PreferencesSection
                  isExpanded={expandedSections.has("preferences")}
                  onToggle={() => toggleSection("preferences")}
                />

                {/* Artists You Know */}
                <YourArtistsSection
                  isExpanded={expandedSections.has("artists")}
                  onToggle={() => toggleSection("artists")}
                  refreshTrigger={refreshTrigger}
                />

                {/* Songs You Know */}
                <YourSongsSection
                  isExpanded={expandedSections.has("songs")}
                  onToggle={() => toggleSection("songs")}
                  refreshTrigger={refreshTrigger}
                />

                {/* Connected Services (collapsed by default, at bottom) */}
                <ConnectedServicesSection
                  isExpanded={expandedSections.has("services")}
                  onToggle={() => toggleSection("services")}
                  onSyncComplete={handleSyncComplete}
                />

                {/* Feedback section (future placeholder) */}
                <div className="rounded-2xl bg-[var(--card)] border border-[var(--card-border)] p-5">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-[var(--secondary)] flex items-center justify-center">
                      <span className="text-lg text-[var(--text-subtle)]">*</span>
                    </div>
                    <div>
                      <h2 className="font-semibold text-[var(--text-subtle)]">
                        {t("feedbackTitle")}
                      </h2>
                      <p className="text-sm text-[var(--text-subtle)]">
                        {t("feedbackDesc")}
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Footer help text */}
              <div className="mt-8 text-center text-sm text-[var(--text-subtle)]">
                <p>
                  {t("dataPowersRecs")}
                </p>
                <p className="mt-1">
                  {isGuest ? (
                    <>
                      <Link href="/login" className="text-[var(--brand-blue)] hover:underline">
                        {t("createAccountToSync")}
                      </Link>{" "}
                      {t("toSyncServices")}
                    </>
                  ) : (
                    <>
                      {t("moreDataBetterRecs")}{" "}
                      <Link href="/quiz" className="text-[var(--brand-blue)] hover:underline">
                        {t("takeTheQuiz")}
                      </Link>{" "}
                      {t("orConnectMore")}
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
