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
          className={`w-3 h-3 ${i < stars ? "text-[#ffeb3b]" : "text-white/20"}`}
        />
      ))}
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const percentage = Math.round(score * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 rounded-full bg-white/10 overflow-hidden">
        <div
          className="h-full rounded-full bg-gradient-to-r from-[#ff2d92] to-[#b347ff]"
          style={{ width: `${percentage}%` }}
        />
      </div>
      <span className="text-xs text-white/40 font-mono">{percentage}%</span>
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
    default:
      return "default";
  }
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
      {/* Glow effect on hover */}
      <div
        className={`absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-[#ff2d92] via-[#b347ff] to-[#00f5ff] opacity-0 blur-sm transition-opacity duration-300 ${
          isHovered ? "opacity-60" : ""
        }`}
      />

      <div className="relative flex flex-col gap-3 p-4 rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 backdrop-blur-sm transition-all duration-300 hover:border-white/20">
        {/* Top row: Title and score */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold text-white truncate group-hover:text-[#00f5ff] transition-colors">
              {recommendation.title}
            </h3>
            <p className="text-sm text-white/60 truncate mt-0.5">{recommendation.artist}</p>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <SparklesIcon className="w-4 h-4 text-[#ffeb3b]" />
            <ScoreBar score={recommendation.score} />
          </div>
        </div>

        {/* Middle row: Reason */}
        <div className="flex items-center gap-2">
          <Badge variant={getReasonBadgeVariant(recommendation.reason_type)}>
            {recommendation.reason}
          </Badge>
        </div>

        {/* Bottom row: Stats and action */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-4">
            {/* Brand count */}
            <div className="flex items-center gap-2">
              <PopularityStars count={recommendation.brand_count} />
              <span className="text-xs text-white/40">{recommendation.brand_count} brands</span>
            </div>

            {/* Spotify popularity */}
            {recommendation.popularity > 0 && (
              <span className="text-xs text-white/30">Pop: {recommendation.popularity}</span>
            )}
          </div>

          {/* Karaoke button with dropdown */}
          <div className="relative" ref={dropdownRef}>
            <button
              className="flex items-center gap-2 px-4 py-2 rounded-full bg-gradient-to-r from-[#ff2d92] to-[#b347ff] text-white text-sm font-semibold transition-all duration-200 hover:scale-105 hover:shadow-[0_0_20px_rgba(255,45,146,0.5)] active:scale-95"
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
              <div className="absolute right-0 mt-2 w-56 rounded-xl bg-[rgba(30,30,40,0.98)] border border-white/20 shadow-xl overflow-hidden z-50 animate-fade-in">
                <div className="p-1">
                  <a
                    href={links.youtube}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/10 transition-colors"
                    onClick={() => setIsDropdownOpen(false)}
                  >
                    <YouTubeIcon className="w-5 h-5 text-red-500" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-white">Search YouTube</div>
                      <div className="text-xs text-white/50 truncate">
                        Find existing karaoke videos
                      </div>
                    </div>
                  </a>

                  <a
                    href={links.generator}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/10 transition-colors"
                    onClick={() => setIsDropdownOpen(false)}
                  >
                    <VideoIcon className="w-5 h-5 text-[#00f5ff]" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-white">Create with Generator</div>
                      <div className="text-xs text-white/50 truncate">
                        Generate custom karaoke video
                      </div>
                    </div>
                  </a>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
