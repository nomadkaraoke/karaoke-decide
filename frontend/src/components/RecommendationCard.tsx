"use client";

import { useEffect, useRef, useState } from "react";
import type { Recommendation } from "@/types";
import {
  ChevronDownIcon,
  MicrophoneIcon,
  SparklesIcon,
  StarIcon,
  VideoIcon,
  YouTubeIcon,
} from "./icons";
import { Badge } from "./ui";

interface RecommendationCardProps {
  recommendation: Recommendation;
  index?: number;
  showAnimation?: boolean;
}

function PopularityStars({ count }: { count: number }) {
  // Convert brand count to 1-5 stars
  const stars = Math.min(5, Math.max(1, Math.ceil(count / 12)));
  return (
    <div className="flex items-center gap-0.5">
      {[...Array(5)].map((_, i) => (
        <StarIcon
          key={i}
          filled={i < stars}
          className={`w-3 h-3 ${i < stars ? "text-[var(--brand-gold)]" : "text-[var(--text-subtle)]"}`}
        />
      ))}
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const percentage = Math.round(score * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full bg-[var(--secondary)] overflow-hidden">
        <div
          className="h-full rounded-full bg-gradient-to-r from-[var(--brand-pink)] to-[var(--brand-purple)]"
          style={{ width: `${percentage}%` }}
        />
      </div>
      <span className="text-xs text-[var(--text-subtle)] font-mono">{percentage}%</span>
    </div>
  );
}

function getReasonBadgeVariant(
  reasonType: string
): "default" | "spotify" | "lastfm" | "quiz" | "success" | "warning" | "error" {
  switch (reasonType) {
    case "known_artist":
      return "spotify";
    case "similar_genre":
      return "quiz";
    case "decade_match":
      return "warning";
    case "crowd_pleaser":
      return "success";
    case "generate_karaoke":
      return "quiz";
    default:
      return "default";
  }
}

/**
 * Format duration from milliseconds to mm:ss
 */
function formatDuration(ms: number | null): string | null {
  if (!ms) return null;
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/**
 * Generate karaoke link URLs for a song
 */
function getKaraokeLinks(artist: string, title: string) {
  const searchQuery = encodeURIComponent(`${artist} ${title} karaoke`);
  const artistParam = encodeURIComponent(artist);
  const titleParam = encodeURIComponent(title);

  return {
    youtube: `https://www.youtube.com/results?search_query=${searchQuery}`,
    generator: `https://gen.nomadkaraoke.com?artist=${artistParam}&title=${titleParam}`,
  };
}

export function RecommendationCard({
  recommendation,
  index = 0,
  showAnimation = true,
}: RecommendationCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const links = getKaraokeLinks(recommendation.artist, recommendation.title);
  const isGenerateOnly = !recommendation.has_karaoke_version;
  const duration = formatDuration(recommendation.duration_ms);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    }

    if (isDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isDropdownOpen]);

  return (
    <div
      className={`group relative ${showAnimation ? "animate-fade-in-up" : ""} ${isDropdownOpen ? "z-10" : ""}`}
      style={showAnimation ? { animationDelay: `${index * 50}ms` } : undefined}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Glow effect on hover - different color for generate-only */}
      <div
        className={`absolute -inset-0.5 rounded-2xl opacity-0 blur-sm transition-opacity duration-300 ${
          isHovered ? "opacity-60" : ""
        } ${
          isGenerateOnly
            ? "bg-gradient-to-r from-[var(--brand-blue)] via-[var(--brand-blue)] to-[var(--brand-purple)]"
            : "bg-gradient-to-r from-[var(--brand-blue)] via-[var(--brand-purple)] to-[var(--brand-pink)]"
        }`}
      />

      <div
        className={`relative flex flex-col gap-3 p-4 rounded-2xl backdrop-blur-sm transition-all duration-300 ${
          isGenerateOnly
            ? "bg-[var(--card)] border border-[var(--brand-blue)]/30 hover:border-[var(--brand-blue)]/50"
            : "bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--text-subtle)]"
        }`}
      >
        {/* Top row: Title and score */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3
                className={`text-lg font-semibold truncate transition-colors ${
                  isGenerateOnly
                    ? "text-[var(--text)] group-hover:text-[var(--brand-blue)]"
                    : "text-[var(--text)] group-hover:text-[var(--brand-pink)]"
                }`}
              >
                {recommendation.title}
              </h3>
              {/* Badges next to title */}
              {recommendation.explicit && (
                <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-bold bg-[var(--secondary)] text-[var(--text-muted)] rounded">
                  E
                </span>
              )}
              {recommendation.is_classic && (
                <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-bold bg-[var(--brand-gold)]/20 text-[var(--brand-gold)] rounded">
                  CLASSIC
                </span>
              )}
            </div>
            <p className="text-sm text-[var(--text-muted)] truncate mt-0.5">{recommendation.artist}</p>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <SparklesIcon className="w-4 h-4 text-[var(--brand-gold)]" />
            <ScoreBar score={recommendation.score} />
          </div>
        </div>

        {/* Middle row: Reason */}
        <div className="flex items-center gap-2">
          <Badge variant={getReasonBadgeVariant(recommendation.reason_type)}>
            {recommendation.reason}
          </Badge>
          {isGenerateOnly && (
            <Badge variant="default">
              Generate Only
            </Badge>
          )}
        </div>

        {/* Bottom row: Stats and action */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-4">
            {/* Brand count - only show for karaoke-ready songs */}
            {recommendation.has_karaoke_version && recommendation.brand_count > 0 && (
              <div className="flex items-center gap-2">
                <PopularityStars count={recommendation.brand_count} />
                <span className="text-xs text-[var(--text-subtle)]">{recommendation.brand_count} brands</span>
              </div>
            )}

            {/* Duration */}
            {duration && (
              <span className="text-xs text-[var(--text-subtle)]">{duration}</span>
            )}

            {/* Spotify popularity */}
            {recommendation.popularity > 0 && (
              <span className="text-xs text-[var(--text-subtle)]">Pop: {recommendation.popularity}</span>
            )}
          </div>

          {/* Action button */}
          <div className="relative" ref={dropdownRef}>
            {isGenerateOnly ? (
              /* Direct generate button for non-karaoke songs */
              <a
                href={links.generator}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--brand-blue)] text-white text-sm font-semibold transition-all duration-200 hover:scale-105 hover:shadow-[0_0_20px_rgba(59,130,246,0.5)] active:scale-95"
              >
                <VideoIcon className="w-4 h-4" />
                <span>Generate</span>
              </a>
            ) : (
              /* Dropdown for karaoke-ready songs */
              <>
                <button
                  className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--brand-pink)] text-white text-sm font-semibold transition-all duration-200 hover:bg-[var(--brand-pink-hover)] hover:scale-105 hover:shadow-[0_0_20px_rgba(255,122,204,0.5)] active:scale-95"
                  onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                >
                  <MicrophoneIcon className="w-4 h-4" />
                  <span>Sing it!</span>
                  <ChevronDownIcon
                    className={`w-4 h-4 transition-transform duration-200 ${isDropdownOpen ? "rotate-180" : ""}`}
                  />
                </button>

                {/* Dropdown menu */}
                {isDropdownOpen && (
                  <div className="absolute right-0 mt-2 w-56 rounded-xl bg-[var(--card)] border border-[var(--card-border)] shadow-xl overflow-hidden z-50 animate-fade-in">
                    <div className="p-1">
                      <a
                        href={links.youtube}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-[var(--secondary)] transition-colors"
                        onClick={() => setIsDropdownOpen(false)}
                      >
                        <YouTubeIcon className="w-5 h-5 text-red-500" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-[var(--text)]">Search YouTube</div>
                          <div className="text-xs text-[var(--text-subtle)] truncate">
                            Find existing karaoke videos
                          </div>
                        </div>
                      </a>

                      <a
                        href={links.generator}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-[var(--secondary)] transition-colors"
                        onClick={() => setIsDropdownOpen(false)}
                      >
                        <VideoIcon className="w-5 h-5 text-[var(--brand-blue)]" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-[var(--text)]">Create with Generator</div>
                          <div className="text-xs text-[var(--text-subtle)] truncate">
                            Generate custom karaoke video
                          </div>
                        </div>
                      </a>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
