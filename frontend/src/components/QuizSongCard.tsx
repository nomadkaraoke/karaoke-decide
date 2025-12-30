"use client";

import type { QuizSong } from "@/types";
import { CheckIcon, StarIcon } from "./icons";

interface QuizSongCardProps {
  song: QuizSong;
  isSelected: boolean;
  onToggle: () => void;
  index?: number;
}

function PopularityStars({ count }: { count: number }) {
  const stars = Math.min(5, Math.max(1, Math.ceil(count / 12)));
  return (
    <div className="flex items-center gap-0.5">
      {[...Array(5)].map((_, i) => (
        <StarIcon
          key={i}
          filled={i < stars}
          className={`w-2.5 h-2.5 ${i < stars ? "text-[#ffeb3b]" : "text-white/20"}`}
        />
      ))}
    </div>
  );
}

export function QuizSongCard({
  song,
  isSelected,
  onToggle,
  index = 0,
}: QuizSongCardProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`
        relative w-full text-left p-4 rounded-2xl border transition-all duration-200
        animate-fade-in-up
        ${
          isSelected
            ? "bg-[#ff2d92]/20 border-[#ff2d92]/50 shadow-[0_0_20px_rgba(255,45,146,0.2)]"
            : "bg-[rgba(20,20,30,0.9)] border-white/10 hover:border-white/20 hover:bg-white/5"
        }
      `}
      style={{ animationDelay: `${index * 30}ms` }}
    >
      {/* Selection indicator */}
      <div
        className={`
          absolute top-3 right-3 w-6 h-6 rounded-full flex items-center justify-center
          transition-all duration-200
          ${
            isSelected
              ? "bg-[#ff2d92] scale-100"
              : "bg-white/10 scale-90"
          }
        `}
      >
        {isSelected && <CheckIcon className="w-4 h-4 text-white" />}
      </div>

      {/* Song info */}
      <div className="pr-10">
        <h3
          className={`font-semibold truncate transition-colors ${
            isSelected ? "text-white" : "text-white/90"
          }`}
        >
          {song.title}
        </h3>
        <p className="text-sm text-white/60 truncate mt-0.5">{song.artist}</p>

        {/* Meta row */}
        <div className="flex items-center gap-3 mt-2">
          <span className="px-2 py-0.5 rounded-full bg-white/10 text-white/60 text-xs">
            {song.decade}
          </span>
          <PopularityStars count={song.brand_count} />
        </div>
      </div>
    </button>
  );
}
