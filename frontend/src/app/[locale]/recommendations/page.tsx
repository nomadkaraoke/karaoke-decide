"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Link, useRouter } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import type {
  Recommendation,
  RecDuration,
  RecHasKaraoke,
  RecLibrary,
  RecSort,
  RecV2Params,
  RecV2Section,
  RecV2Song,
} from "@/types";
import { ProtectedPage } from "@/components/ProtectedPage";
import { RecommendationCard } from "@/components/RecommendationCard";
import { SparklesIcon, MusicIcon, LinkIcon } from "@/components/icons";
import { Button, LoadingPulse, EmptyState } from "@/components/ui";

// Filter types
type KaraokeFilter = "all" | "karaoke" | "generate";
type PopularityFilter = "any" | "hidden-gems" | "somewhat-known" | "popular" | "chart-toppers";
type DurationFilter = "any" | RecDuration;

const SORTS: RecSort[] = ["best", "popular", "newest", "oldest", "shortest", "az"];
const LIBRARIES: RecLibrary[] = ["any", "community", "karafun"];
const SORT_LABEL_KEY: Record<RecSort, string> = {
  best: "sortBest",
  popular: "sortPopular",
  newest: "sortNewest",
  oldest: "sortOldest",
  shortest: "sortShortest",
  az: "sortAz",
};
const LIBRARY_LABEL_KEY: Record<RecLibrary, string> = {
  any: "libraryAny",
  community: "libraryCommunity",
  karafun: "libraryKarafun",
};

// Popularity filter -> v2 params (popularity = min, popularity_max = max; both inclusive)
const POPULARITY_PARAMS: Record<PopularityFilter, Pick<RecV2Params, "popularity" | "popularity_max">> = {
  any: {},
  "hidden-gems": { popularity_max: 29 },
  "somewhat-known": { popularity: 30, popularity_max: 49 },
  popular: { popularity: 50, popularity_max: 69 },
  "chart-toppers": { popularity: 70 },
};

const KARAOKE_PARAM: Record<KaraokeFilter, RecHasKaraoke> = {
  all: "any",
  karaoke: "yes",
  generate: "make",
};

// Seconds before a warming_up (503) response is retried automatically
const WARMING_RETRY_MS = 10000;

function toCardRecommendation(song: RecV2Song): Recommendation {
  return {
    song_id: song.song_key,
    artist: song.artist,
    title: song.title,
    score: song.score,
    reason: song.reason,
    reason_type: song.reason_type,
    reason_artist: song.reason_artist,
    brand_count: song.karaoke.brand_count,
    popularity: song.facets.popularity ?? 0,
    has_karaoke_version: song.has_karaoke_version,
    is_classic: song.facets.is_classic,
    duration_ms: song.facets.duration_ms,
    explicit: song.facets.explicit,
    community: song.karaoke.community,
    karafun: song.karaoke.karafun,
  };
}

export default function RecommendationsPage() {
  const router = useRouter();
  const t = useTranslations("recommendations");
  const tRoot = useTranslations();
  const { hasCompletedQuiz, quizStatusLoading } = useAuth();
  const [sections, setSections] = useState<RecV2Section[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isWarming, setIsWarming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [karaokeFilter, setKaraokeFilter] = useState<KaraokeFilter>("all");
  const [popularityFilter, setPopularityFilter] = useState<PopularityFilter>("any");
  const [excludeExplicit, setExcludeExplicit] = useState(false);
  const [classicsOnly, setClassicsOnly] = useState(false);
  const [durationFilter, setDurationFilter] = useState<DurationFilter>("any");
  const [sort, setSort] = useState<RecSort>("best");
  const [library, setLibrary] = useState<RecLibrary>("any");

  // Collapsed sections
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());

  const POPULARITY_LABELS: Record<PopularityFilter, string> = {
    any: t("anyPopularity"),
    "hidden-gems": t("hiddenGems"),
    "somewhat-known": t("somewhatKnown"),
    popular: t("popular"),
    "chart-toppers": t("chartToppers"),
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

  const buildParams = useCallback((): RecV2Params => {
    const params: RecV2Params = { sort, ...POPULARITY_PARAMS[popularityFilter] };
    if (karaokeFilter !== "all") params.has_karaoke = KARAOKE_PARAM[karaokeFilter];
    if (durationFilter !== "any") params.duration = durationFilter;
    if (excludeExplicit) params.explicit = "exclude";
    if (classicsOnly) params.classic = true;
    if (library !== "any") params.library = library;
    return params;
  }, [sort, popularityFilter, karaokeFilter, durationFilter, excludeExplicit, classicsOnly, library]);

  // Guards against out-of-order responses when filters change quickly
  const requestId = useRef(0);

  const loadRecommendations = useCallback(async () => {
    const id = ++requestId.current;
    try {
      setIsLoading(true);
      setError(null);
      setIsWarming(false);
      const response = await api.my.getRecommendationsV2(buildParams());
      if (id !== requestId.current) return;
      setSections(response.sections);
      setNextCursor(response.next_cursor);
    } catch (err) {
      if (id !== requestId.current) return;
      if (err instanceof ApiError && err.status === 503) {
        setIsWarming(true);
      } else {
        setError(err instanceof Error ? err.message : t("failedToLoadRecs"));
      }
    } finally {
      if (id === requestId.current) setIsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildParams]);

  useEffect(() => {
    loadRecommendations();
  }, [loadRecommendations]);

  // Auto-retry while the catalog is warming up
  useEffect(() => {
    if (!isWarming) return;
    const timer = setTimeout(loadRecommendations, WARMING_RETRY_MS);
    return () => clearTimeout(timer);
  }, [isWarming, loadRecommendations]);

  const loadMore = async () => {
    if (!nextCursor) return;
    const id = requestId.current;
    try {
      setIsLoadingMore(true);
      const response = await api.my.getRecommendationsV2({ ...buildParams(), cursor: nextCursor });
      if (id !== requestId.current) return;
      setSections((prev) => {
        const incoming = response.sections;
        if (prev.length === 0) return incoming;
        const next = prev.map((sec) => ({ ...sec }));
        for (const sec of incoming) {
          const existing = next.find((x) => x.id === sec.id);
          if (existing) existing.songs = [...existing.songs, ...sec.songs];
          else next.push(sec);
        }
        return next;
      });
      setNextCursor(response.next_cursor);
    } catch (err) {
      if (id === requestId.current) {
        setError(err instanceof Error ? err.message : t("failedToLoadRecs"));
      }
    } finally {
      setIsLoadingMore(false);
    }
  };

  const hasActiveFilters =
    karaokeFilter !== "all" ||
    popularityFilter !== "any" ||
    excludeExplicit ||
    classicsOnly ||
    durationFilter !== "any" ||
    library !== "any";

  const clearFilters = () => {
    setKaraokeFilter("all");
    setPopularityFilter("any");
    setExcludeExplicit(false);
    setClassicsOnly(false);
    setDurationFilter("any");
    setLibrary("any");
  };

  const visibleSections = sections.filter((sec) => sec.songs.length > 0);

  const renderSection = (section: RecV2Section) => {
    const isCollapsed = collapsedSections.has(section.id);
    const count = section.songs.length;
    const title = tRoot(section.title_key, section.title_params);
    const subtitle = tRoot(section.reason_key, section.title_params);

    return (
      <div key={section.id} className="mb-8" data-testid={`rec-section-${section.id}`}>
        <button
          onClick={() => toggleSection(section.id)}
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
            {section.songs.map((song, index) => (
              <RecommendationCard
                key={song.song_key}
                recommendation={toCardRecommendation(song)}
                index={index}
              />
            ))}
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
                  {Object.entries(POPULARITY_LABELS).map(([key, label]) => (
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
                  onChange={(e) => setDurationFilter(e.target.value as DurationFilter)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  <option value="any">{t("anyLength")}</option>
                  <option value="short">{t("shortDuration")}</option>
                  <option value="medium">{t("mediumDuration")}</option>
                  <option value="long">{t("longDuration")}</option>
                </select>
              </div>

              {/* Library filter */}
              <div>
                <label htmlFor="rec-library" className="text-xs text-[var(--text-subtle)] mb-1 block">{t("libraryLabel")}</label>
                <select
                  id="rec-library"
                  value={library}
                  onChange={(e) => setLibrary(e.target.value as RecLibrary)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  {LIBRARIES.map((value) => (
                    <option key={value} value={value}>
                      {t(LIBRARY_LABEL_KEY[value])}
                    </option>
                  ))}
                </select>
                {library === "karafun" && (
                  <p className="text-xs text-[var(--text-subtle)] mt-1">{t("libraryKarafunInfo")}</p>
                )}
              </div>

              {/* Sort */}
              <div>
                <label htmlFor="rec-sort" className="text-xs text-[var(--text-subtle)] mb-1 block">{t("sortLabel")}</label>
                <select
                  id="rec-sort"
                  value={sort}
                  onChange={(e) => setSort(e.target.value as RecSort)}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text)] text-sm focus:outline-none focus:border-[var(--card-border)]"
                >
                  {SORTS.map((value) => (
                    <option key={value} value={value}>
                      {t(SORT_LABEL_KEY[value])}
                    </option>
                  ))}
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
          ) : isWarming ? (
            <div className="flex flex-col items-center justify-center py-16 text-center" data-testid="recs-warming">
              <LoadingPulse count={2} />
              <p className="text-[var(--text)] font-medium mt-6 mb-1">{t("warmingTitle")}</p>
              <p className="text-[var(--text-muted)] text-sm mb-4">{t("warmingDescription")}</p>
              <Button onClick={loadRecommendations} variant="secondary">
                {t("tryAgain")}
              </Button>
            </div>
          ) : visibleSections.length === 0 ? (
            hasActiveFilters ? (
              <EmptyState
                icon={<SparklesIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
                title={t("noMatchTitle")}
                description={t("noMatchDescription")}
                action={{
                  label: t("clearAllFilters"),
                  onClick: clearFilters,
                }}
              />
            ) : (
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
            )
          ) : (
            <>
              {visibleSections.map(renderSection)}

              {nextCursor && (
                <div className="flex justify-center mb-8">
                  <Button variant="secondary" onClick={loadMore} disabled={isLoadingMore}>
                    {isLoadingMore ? t("loadingMore") : t("loadMore")}
                  </Button>
                </div>
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
