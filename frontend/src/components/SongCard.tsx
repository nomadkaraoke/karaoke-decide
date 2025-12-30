"use client";

import { useState } from "react";
import { MicrophoneIcon, StarIcon } from "./icons";

interface Song {
  id: number | string;
  artist: string;
  title: string;
  brandCount: number;
}

interface SongCardProps {
  song: Song;
  index?: number;
  showAnimation?: boolean;
}

export function PopularityStars({ count }: { count: number }) {
  // Convert brand count to 1-5 stars
  const stars = Math.min(5, Math.max(1, Math.ceil(count / 12)));
  return (
    <div className="flex items-center gap-0.5">
      {[...Array(5)].map((_, i) => (
        <StarIcon
          key={i}
          filled={i < stars}
          className={`w-3.5 h-3.5 ${i < stars ? "text-[#ffeb3b]" : "text-white/20"}`}
        />
      ))}
      <span className="ml-1.5 text-xs text-white/40 font-mono">{count}</span>
    </div>
  );
}

export function SongCard({ song, index = 0, showAnimation = true }: SongCardProps) {
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
        className={`absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-[#ff2d92] via-[#b347ff] to-[#00f5ff] opacity-0 blur-sm transition-opacity duration-300 ${
          isHovered ? "opacity-60" : ""
        }`}
      />

      <div className="relative flex flex-col gap-3 p-4 rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 backdrop-blur-sm transition-all duration-300 hover:border-white/20">
        {/* Song info */}
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold text-white truncate group-hover:text-[#00f5ff] transition-colors">
            {song.title}
          </h3>
          <p className="text-sm text-white/60 truncate mt-0.5">{song.artist}</p>
        </div>

        {/* Bottom row */}
        <div className="flex items-center justify-between gap-3">
          <PopularityStars count={song.brandCount} />

          <button
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-gradient-to-r from-[#ff2d92] to-[#b347ff] text-white text-sm font-semibold transition-all duration-200 hover:scale-105 hover:shadow-[0_0_20px_rgba(255,45,146,0.5)] active:scale-95"
            onClick={() => {
              // Will link to karaoke video or generator
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
