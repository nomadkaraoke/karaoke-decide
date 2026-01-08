"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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

// Popularity filter ranges
const POPULARITY_RANGES: Record<PopularityFilter, { min?: number; max?: number; label: string }> = {
  any: { label: "Any popularity" },
  "hidden-gems": { max: 30, label: "Hidden gems" },
  "somewhat-known": { min: 30, max: 50, label: "Somewhat known" },
  popular: { min: 50, max: 70, label: "Popular" },
  "chart-toppers": { min: 70, label: "Chart toppers" },
};

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
        err instanceof Error ? err.message : "Failed to load recommendations"
      );
    } finally {
      setIsLoading(false);
    }
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
              Recommendations
            </h1>
            <p className="text-[var(--text-muted)] text-sm mt-1">
              Songs you might love, based on your music taste
            </p>
          </div>

          {/* Quiz Prompt Banner - show if quiz not completed */}
          {!quizStatusLoading && !hasCompletedQuiz && (
            <div className="mb-6 p-4 rounded-xl bg-gradient-to-r from-[var(--brand-pink)]/10 to-[var(--brand-purple)]/10 border border-[var(--brand-pink)]/30">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                  <h3 className="text-[var(--text)] font-semibold flex items-center gap-2">
                    <span className="text-xl">✨</span>
                    Get personalized recommendations
                  </h3>
                  <p className="text-[var(--text-muted)] text-sm mt-1">
                    Take a quick 30-second quiz to tell us your music taste
                  </p>
                </div>
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => router.push("/quiz")}
                  className="whitespace-nowrap"
                >
                  Take Quiz
                </Button>
              </div>
            </div>
          )}

          {/* Filters */}
          <div className="bg-[var(--card)] rounded-xl p-4 mb-6 border border-[var(--card-border)]">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-[var(--text)]">Filters</span>
              {hasActiveFilters && (
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  Clear all
                </Button>
              )}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {/* Karaoke availability filter */}
              <div>
                <label className="text-xs text-[var(--text-subtle)] mb-1 block">Show</label>
                <select
                  value={karaokeFilter}
                  onChange={(e) => setKaraokeFilter(e.target.value as KaraokeFilter)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  <option value="all">All songs</option>
                  <option value="karaoke">Karaoke ready</option>
                  <option value="generate">Generate only</option>
                </select>
              </div>

              {/* Popularity filter */}
              <div>
                <label className="text-xs text-[var(--text-subtle)] mb-1 block">Popularity</label>
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
                <label className="text-xs text-[var(--text-subtle)] mb-1 block">Duration</label>
                <select
                  value={durationFilter}
                  onChange={(e) =>
                    setDurationFilter(e.target.value as "any" | "short" | "medium" | "long")
                  }
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  <option value="any">Any length</option>
                  <option value="short">Short (&lt;3 min)</option>
                  <option value="medium">Medium (3-5 min)</option>
                  <option value="long">Long (&gt;5 min)</option>
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
                Hide explicit
              </label>
              <label className="flex items-center gap-2 text-sm text-[var(--text-muted)] cursor-pointer">
                <input
                  type="checkbox"
                  checked={classicsOnly}
                  onChange={(e) => setClassicsOnly(e.target.checked)}
                  className="w-4 h-4 rounded bg-[var(--secondary)] border-[var(--card-border)] text-[#1ed760] focus:ring-[#1ed760]/50"
                />
                Classics only
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
                Try again
              </Button>
            </div>
          ) : !data || data.total_count === 0 ? (
            <EmptyState
              icon={<SparklesIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
              title="No recommendations yet"
              description="Connect your music services or take the quiz to get personalized recommendations."
              action={{
                label: "Connect Services",
                onClick: () => router.push("/services"),
              }}
              secondaryAction={{
                label: "Take the Quiz",
                onClick: () => router.push("/quiz"),
              }}
            />
          ) : (
            <>
              {/* Categorized sections */}
              {renderSection(
                "From Artists You Know",
                "Karaoke songs by artists in your library",
                data.from_artists_you_know,
                "artists",
                !hasCompletedQuiz
                  ? "Take the quiz above to tell us your favorite artists"
                  : hasActiveFilters
                    ? "No karaoke songs from your artists match the current filters"
                    : "Connect Spotify or Last.fm to see songs from artists you know"
              )}

              {renderSection(
                "Create Your Own Karaoke",
                "Songs from your library - generate with AI",
                data.create_your_own,
                "create",
                !hasCompletedQuiz
                  ? "Take the quiz to unlock personalized song suggestions"
                  : hasActiveFilters
                    ? "No songs match the current filters"
                    : "Connect your music services to see songs you can generate karaoke for"
              )}

              {renderSection(
                "Crowd Pleasers",
                "Popular karaoke songs everyone knows",
                data.crowd_pleasers,
                "crowd",
                "No crowd pleasers match the current filters"
              )}

              {/* Footer */}
              <div className="mt-8 pt-6 border-t border-[var(--card-border)]">
                <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
                  <Link
                    href="/my-songs"
                    className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <MusicIcon className="w-4 h-4" />
                    View my songs
                  </Link>
                  <Link
                    href="/services"
                    className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <LinkIcon className="w-4 h-4" />
                    Sync more music
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
