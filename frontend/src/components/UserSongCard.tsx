"use client";

import { useState } from "react";
import { MicrophoneIcon, StarIcon } from "./icons";
import { SourceBadge } from "./ui";

interface UserSong {
  id: string;
  song_id: string;
  artist: string;
  title: string;
  source: "spotify" | "lastfm" | "quiz";
  play_count: number;
  is_saved: boolean;
  times_sung: number;
}

interface UserSongCardProps {
  song: UserSong;
  index?: number;
  showAnimation?: boolean;
}

function PopularityStars({ count }: { count: number }) {
  // Convert play count to 1-5 stars (scale: 0-50+ plays)
  const stars = Math.min(5, Math.max(1, Math.ceil(count / 10)));
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

export function UserSongCard({
  song,
  index = 0,
  showAnimation = true,
}: UserSongCardProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className={`group relative ${showAnimation ? "animate-fade-in-up" : ""}`}
      style={showAnimation ? { animationDelay: `${index * 50}ms` } : undefined}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Glow effect on hover */}
      <div
        className={`absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-[var(--brand-blue)] via-[var(--brand-purple)] to-[var(--brand-pink)] opacity-0 blur-sm transition-opacity duration-300 ${
          isHovered ? "opacity-60" : ""
        }`}
      />

      <div className="relative flex flex-col gap-3 p-4 rounded-2xl bg-[var(--card)] border border-[var(--card-border)] backdrop-blur-sm transition-all duration-300 hover:border-[var(--text-subtle)]">
        {/* Top row: Title and source badge */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <h3 className="text-lg font-semibold text-[var(--text)] truncate group-hover:text-[var(--brand-pink)] transition-colors">
              {song.title}
            </h3>
            <p className="text-sm text-[var(--text-muted)] truncate mt-0.5">{song.artist}</p>
          </div>
          <SourceBadge source={song.source} />
        </div>

        {/* Bottom row: Stats and action */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-4">
            {/* Play count */}
            <div className="flex items-center gap-2">
              <PopularityStars count={song.play_count} />
              <span className="text-xs text-[var(--text-subtle)] font-mono">
                {song.play_count} plays
              </span>
            </div>

            {/* Times sung */}
            {song.times_sung > 0 && (
              <span className="text-xs text-[var(--brand-purple)]">
                Sung {song.times_sung}x
              </span>
            )}
          </div>

          <button
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-[var(--brand-pink)] text-[var(--text)] text-sm font-semibold transition-all duration-200 hover:bg-[var(--brand-pink-hover)] hover:scale-105 hover:shadow-[0_0_20px_rgba(255,122,204,0.5)] active:scale-95"
            onClick={() => {
              window.open(
                `https://www.youtube.com/results?search_query=${encodeURIComponent(
                  `${song.artist} ${song.title} karaoke`
                )}`,
                "_blank",
                "noopener,noreferrer"
              );
            }}
          >
            <MicrophoneIcon className="w-4 h-4" />
            <span>Sing it!</span>
          </button>
        </div>
      </div>
    </div>
  );
}
