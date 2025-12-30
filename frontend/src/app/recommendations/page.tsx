"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { ProtectedPage } from "@/components/ProtectedPage";
import { RecommendationCard } from "@/components/RecommendationCard";
import { SparklesIcon, MusicIcon, LinkIcon } from "@/components/icons";
import { Button, LoadingPulse, EmptyState } from "@/components/ui";

interface Recommendation {
  song_id: string;
  artist: string;
  title: string;
  score: number;
  reason: string;
  reason_type: string;
  brand_count: number;
  popularity: number;
}

const DECADES = ["1970s", "1980s", "1990s", "2000s", "2010s", "2020s"];

export default function RecommendationsPage() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [decade, setDecade] = useState<string | null>(null);
  const [minPopularity, setMinPopularity] = useState<number | null>(null);

  const loadRecommendations = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);

      const response = await api.my.getRecommendations(
        30,
        decade || undefined,
        minPopularity || undefined
      );

      setRecommendations(response.recommendations);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load recommendations"
      );
    } finally {
      setIsLoading(false);
    }
  }, [decade, minPopularity]);

  useEffect(() => {
    loadRecommendations();
  }, [loadRecommendations]);

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-white flex items-center gap-3">
              <SparklesIcon className="w-7 h-7 text-[#ffeb3b]" />
              Recommendations
            </h1>
            <p className="text-white/60 text-sm mt-1">
              Songs you might love, based on your music taste
            </p>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3 mb-6">
            {/* Decade filter */}
            <select
              value={decade || ""}
              onChange={(e) => setDecade(e.target.value || null)}
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:outline-none focus:border-white/20"
            >
              <option value="">All decades</option>
              {DECADES.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>

            {/* Popularity filter */}
            <select
              value={minPopularity || ""}
              onChange={(e) =>
                setMinPopularity(e.target.value ? parseInt(e.target.value) : null)
              }
              className="px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white text-sm focus:outline-none focus:border-white/20"
            >
              <option value="">Any popularity</option>
              <option value="30">Somewhat popular</option>
              <option value="50">Popular</option>
              <option value="70">Very popular</option>
            </select>

            {/* Clear filters */}
            {(decade || minPopularity) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setDecade(null);
                  setMinPopularity(null);
                }}
              >
                Clear filters
              </Button>
            )}
          </div>

          {/* Content */}
          {isLoading ? (
            <LoadingPulse count={5} />
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                <span className="text-2xl">⚠️</span>
              </div>
              <p className="text-white/60 mb-4">{error}</p>
              <Button onClick={loadRecommendations} variant="secondary">
                Try again
              </Button>
            </div>
          ) : recommendations.length === 0 ? (
            <EmptyState
              icon={<SparklesIcon className="w-8 h-8 text-white/20" />}
              title="No recommendations yet"
              description="Connect your music services or take the quiz to get personalized recommendations."
              action={{
                label: "Connect Services",
                onClick: () => (window.location.href = "/services"),
              }}
              secondaryAction={{
                label: "Take the Quiz",
                onClick: () => (window.location.href = "/quiz"),
              }}
            />
          ) : (
            <>
              {/* Recommendation list */}
              <div className="flex flex-col gap-3">
                {recommendations.map((rec, index) => (
                  <RecommendationCard
                    key={rec.song_id}
                    recommendation={rec}
                    index={index}
                  />
                ))}
              </div>

              {/* Footer */}
              <div className="mt-8 pt-6 border-t border-white/10">
                <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
                  <Link
                    href="/my-songs"
                    className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
                  >
                    <MusicIcon className="w-4 h-4" />
                    View my songs
                  </Link>
                  <Link
                    href="/services"
                    className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
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
