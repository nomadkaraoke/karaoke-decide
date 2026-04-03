"use client";

import { useState, useEffect, useCallback } from "react";
import { Link, useRouter } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import type { Recommendation } from "@/types";
import { ProtectedPage } from "@/components/ProtectedPage";
import { RecommendationCard } from "@/components/RecommendationCard";
import { SparklesIcon, MusicIcon, LinkIcon } from "@/components/icons";
import { Button, LoadingPulse, EmptyState } from "@/components/ui";

// Filter types
type KaraokeFilter = "all" | "karaoke" | "generate";
type PopularityFilter = "any" | "hidden-gems" | "somewhat-known" | "popular" | "chart-toppers";

// Duration constants (in ms)
const SHORT_DURATION = 3 * 60 * 1000; // 3 minutes
const LONG_DURATION = 5 * 60 * 1000; // 5 minutes

interface CategorizedData {
  from_artists_you_know: Recommendation[];
  create_your_own: Recommendation[];
  crowd_pleasers: Recommendation[];
  total_count: number;
}

export default function RecommendationsPage() {
  const router = useRouter();
  const t = useTranslations("recommendations");
  const { hasCompletedQuiz, quizStatusLoading } = useAuth();
  const [data, setData] = useState<CategorizedData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [karaokeFilter, setKaraokeFilter] = useState<KaraokeFilter>("all");
  const [popularityFilter, setPopularityFilter] = useState<PopularityFilter>("any");
  const [excludeExplicit, setExcludeExplicit] = useState(false);
  const [classicsOnly, setClassicsOnly] = useState(false);
  const [durationFilter, setDurationFilter] = useState<"any" | "short" | "medium" | "long">("any");

  // Collapsed sections
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());

  // Popularity filter ranges - use translated labels
  const POPULARITY_RANGES: Record<PopularityFilter, { min?: number; max?: number; label: string }> = {
    any: { label: t("anyPopularity") },
    "hidden-gems": { max: 30, label: t("hiddenGems") },
    "somewhat-known": { min: 30, max: 50, label: t("somewhatKnown") },
    popular: { min: 50, max: 70, label: t("popular") },
    "chart-toppers": { min: 70, label: t("chartToppers") },
  };

  const toggleSection = (section: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  };

  const loadRecommendations = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      // Build filter params
      const filters: {
        has_karaoke?: boolean;
        min_popularity?: number;
        max_popularity?: number;
        exclude_explicit?: boolean;
        min_duration_ms?: number;
        max_duration_ms?: number;
        classics_only?: boolean;
      } = {};

      // Karaoke filter
      if (karaokeFilter === "karaoke") {
        filters.has_karaoke = true;
      } else if (karaokeFilter === "generate") {
        filters.has_karaoke = false;
      }

      // Popularity filter
      const popRange = POPULARITY_RANGES[popularityFilter];
      if (popRange.min !== undefined) {
        filters.min_popularity = popRange.min;
      }
      if (popRange.max !== undefined) {
        filters.max_popularity = popRange.max;
      }

      // Explicit filter
      if (excludeExplicit) {
        filters.exclude_explicit = true;
      }

      // Classics filter
      if (classicsOnly) {
        filters.classics_only = true;
      }

      // Duration filter
      if (durationFilter === "short") {
        filters.max_duration_ms = SHORT_DURATION;
      } else if (durationFilter === "medium") {
        filters.min_duration_ms = SHORT_DURATION;
        filters.max_duration_ms = LONG_DURATION;
      } else if (durationFilter === "long") {
        filters.min_duration_ms = LONG_DURATION;
      }

      const response = await api.my.getCategorizedRecommendations(
        Object.keys(filters).length > 0 ? filters : undefined
      );

      setData(response);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("failedToLoadRecs")
      );
    } finally {
      setIsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [karaokeFilter, popularityFilter, excludeExplicit, classicsOnly, durationFilter]);

  useEffect(() => {
    loadRecommendations();
  }, [loadRecommendations]);

  const hasActiveFilters =
    karaokeFilter !== "all" ||
    popularityFilter !== "any" ||
    excludeExplicit ||
    classicsOnly ||
    durationFilter !== "any";

  const clearFilters = () => {
    setKaraokeFilter("all");
    setPopularityFilter("any");
    setExcludeExplicit(false);
    setClassicsOnly(false);
    setDurationFilter("any");
  };

  const renderSection = (
    title: string,
    subtitle: string,
    recommendations: Recommendation[],
    sectionKey: string,
    emptyMessage: string
  ) => {
    const isCollapsed = collapsedSections.has(sectionKey);
    const count = recommendations.length;

    return (
      <div key={sectionKey} className="mb-8">
        <button
          onClick={() => toggleSection(sectionKey)}
          className="w-full text-left mb-4 group"
        >
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-[var(--text)] flex items-center gap-2">
                {title}
                <span className="text-sm font-normal text-[var(--text-subtle)]">
                  ({count})
                </span>
              </h2>
              <p className="text-sm text-[var(--text-subtle)]">{subtitle}</p>
            </div>
            <span
              className={`text-[var(--text-subtle)] transition-transform ${
                isCollapsed ? "" : "rotate-180"
              }`}
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </span>
          </div>
        </button>

        {!isCollapsed && (
          <div className="flex flex-col gap-3">
            {count === 0 ? (
              <p className="text-[var(--text-subtle)] text-sm py-4 text-center">
                {emptyMessage}
              </p>
            ) : (
              recommendations.map((rec, index) => (
                <RecommendationCard
                  key={rec.song_id}
                  recommendation={rec}
                  index={index}
                />
              ))
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
              <SparklesIcon className="w-7 h-7 text-[var(--brand-gold)]" />
              {t("title")}
            </h1>
            <p className="text-[var(--text-muted)] text-sm mt-1">
              {t("subtitle")}
            </p>
          </div>

          {/* Quiz Prompt Banner - show if quiz not completed */}
          {!quizStatusLoading && !hasCompletedQuiz && (
            <div className="mb-6 p-4 rounded-xl bg-gradient-to-r from-[var(--brand-pink)]/10 to-[var(--brand-purple)]/10 border border-[var(--brand-pink)]/30">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <h3 className="text-[var(--text)] font-semibold flex items-center gap-2">
                    <span className="text-xl">✨</span>
                    {t("getPersonalized")}
                  </h3>
                  <p className="text-[var(--text-muted)] text-sm mt-1">
                    {t("takeQuickQuiz")}
                  </p>
                </div>
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => router.push("/quiz")}
                  className="whitespace-nowrap"
                >
                  {t("takeQuiz")}
                </Button>
              </div>
            </div>
          )}

          {/* Filters */}
          <div className="bg-[var(--card)] rounded-xl p-4 mb-6 border border-[var(--card-border)]">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-[var(--text)]">{t("filters")}</span>
              {hasActiveFilters && (
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  {t("clearAllFilters")}
                </Button>
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {/* Karaoke availability filter */}
              <div>
                <label className="text-xs text-[var(--text-subtle)] mb-1 block">{t("showLabel")}</label>
                <select
                  value={karaokeFilter}
                  onChange={(e) => setKaraokeFilter(e.target.value as KaraokeFilter)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  <option value="all">{t("allSongs")}</option>
                  <option value="karaoke">{t("karaokeReady")}</option>
                  <option value="generate">{t("generateOnly")}</option>
                </select>
              </div>

              {/* Popularity filter */}
              <div>
                <label className="text-xs text-[var(--text-subtle)] mb-1 block">{t("popularityLabel")}</label>
                <select
                  value={popularityFilter}
                  onChange={(e) => setPopularityFilter(e.target.value as PopularityFilter)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  {Object.entries(POPULARITY_RANGES).map(([key, { label }]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Duration filter */}
              <div>
                <label className="text-xs text-[var(--text-subtle)] mb-1 block">{t("durationLabel")}</label>
                <select
                  value={durationFilter}
                  onChange={(e) =>
                    setDurationFilter(e.target.value as "any" | "short" | "medium" | "long")
                  }
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  <option value="any">{t("anyLength")}</option>
                  <option value="short">{t("shortDuration")}</option>
                  <option value="medium">{t("mediumDuration")}</option>
                  <option value="long">{t("longDuration")}</option>
                </select>
              </div>
            </div>

            {/* Toggle filters */}
            <div className="flex flex-wrap gap-3 mt-3">
              <label className="flex items-center gap-2 text-sm text-[var(--text-muted)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={excludeExplicit}
                  onChange={(e) => setExcludeExplicit(e.target.checked)}
                  className="w-4 h-4 rounded bg-[var(--secondary)] border-[var(--card-border)] text-[#1ed760] focus:ring-[#1ed760]/50"
                />
                {t("hideExplicit")}
              </label>
              <label className="flex items-center gap-2 text-sm text-[var(--text-muted)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={classicsOnly}
                  onChange={(e) => setClassicsOnly(e.target.checked)}
                  className="w-4 h-4 rounded bg-[var(--secondary)] border-[var(--card-border)] text-[#1ed760] focus:ring-[#1ed760]/50"
                />
                {t("classicsOnly")}
              </label>
            </div>
          </div>

          {/* Content */}
          {isLoading ? (
            <LoadingPulse count={5} />
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                <span className="text-2xl">⚠️</span>
              </div>
              <p className="text-[var(--text-muted)] mb-4">{error}</p>
              <Button onClick={loadRecommendations} variant="secondary">
                {t("tryAgain")}
              </Button>
            </div>
          ) : !data || data.total_count === 0 ? (
            <EmptyState
              icon={<SparklesIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
              title={t("noRecsYet")}
              description={t("noRecsDescription")}
              action={{
                label: t("connectServices"),
                onClick: () => router.push("/services"),
              }}
              secondaryAction={{
                label: t("takeTheQuiz"),
                onClick: () => router.push("/quiz"),
              }}
            />
          ) : (
            <>
              {/* Categorized sections */}
              {renderSection(
                t("fromArtistsYouKnow"),
                t("fromArtistsSubtitle"),
                data.from_artists_you_know,
                "artists",
                !hasCompletedQuiz
                  ? t("fromArtistsEmptyQuiz")
                  : hasActiveFilters
                    ? t("fromArtistsEmptyFilters")
                    : t("fromArtistsEmptyServices")
              )}

              {renderSection(
                t("createYourOwn"),
                t("createYourOwnSubtitle"),
                data.create_your_own,
                "create",
                !hasCompletedQuiz
                  ? t("createYourOwnEmptyQuiz")
                  : hasActiveFilters
                    ? t("createYourOwnEmptyFilters")
                    : t("createYourOwnEmptyServices")
              )}

              {renderSection(
                t("crowdPleasers"),
                t("crowdPleasersSubtitle"),
                data.crowd_pleasers,
                "crowd",
                t("crowdPleasersEmpty")
              )}

              {/* Footer */}
              <div className="mt-8 pt-6 border-t border-[var(--card-border)]">
                <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
                  <Link
                    href="/my-songs"
                    className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <MusicIcon className="w-4 h-4" />
                    {t("viewMySongs")}
                  </Link>
                  <Link
                    href="/services"
                    className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <LinkIcon className="w-4 h-4" />
                    {t("syncMoreMusic")}
                  </Link>
                </div>
              </div>
            </>
          )}
        </div>
      </main>
    </ProtectedPage>
  );
}
