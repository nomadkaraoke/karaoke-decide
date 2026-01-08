"use client";

import { CheckIcon, MicrophoneIcon } from "@/components/icons";

interface QuizArtist {
  name: string;
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;
  genres?: string[];
  image_url: string | null;
}

interface QuizArtistCardProps {
  artist: QuizArtist;
  isSelected: boolean;
  onToggle: () => void;
  index: number;
}

/**
 * Format a Spotify genre for display (e.g., "classic rock" -> "Classic Rock")
 */
function formatGenre(genre: string): string {
  return genre
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function QuizArtistCard({
  artist,
  isSelected,
  onToggle,
  index,
}: QuizArtistCardProps) {
  const displayGenres = artist.genres?.slice(0, 3) || [];

  return (
    <button
      data-testid={`artist-card-${index}`}
      onClick={onToggle}
      className={`
        relative w-full p-4 rounded-xl text-left transition-all duration-200
        ${
          isSelected
            ? "bg-gradient-to-r from-[var(--brand-pink)]/20 to-[var(--brand-purple)]/20 border-[var(--brand-pink)]/50 border-2 scale-[1.02]"
            : "bg-[var(--secondary)] border border-[var(--card-border)] hover:border-[var(--text-subtle)] hover:bg-[var(--card)]"
        }
      `}
      style={{
        animationDelay: `${index * 50}ms`,
      }}
    >
      {/* Selection indicator */}
      <div
        className={`
          absolute top-3 right-3 w-6 h-6 rounded-full flex items-center justify-center
          transition-all duration-200
          ${
            isSelected
              ? "bg-[var(--brand-pink)] scale-100"
              : "bg-[var(--secondary)] scale-90"
          }
        `}
      >
        {isSelected && <CheckIcon className="w-4 h-4 text-[var(--text)]" />}
      </div>

      <div className="flex items-start gap-4 pr-8">
        {/* Artist avatar placeholder */}
        <div
          className={`
            w-14 h-14 rounded-full flex items-center justify-center flex-shrink-0
            ${isSelected ? "bg-[var(--brand-pink)]/30" : "bg-[var(--secondary)]"}
          `}
        >
          <MicrophoneIcon
            className={`w-6 h-6 ${isSelected ? "text-[var(--brand-pink)]" : "text-[var(--text-muted)]"}`}
          />
        </div>

        {/* Artist info */}
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-[var(--text)] truncate text-lg">
            {artist.name}
          </h3>
          <p className="text-[var(--text-muted)] text-sm">
            {artist.song_count} karaoke songs
          </p>

          {/* Genre pills */}
          {displayGenres.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {displayGenres.map((genre, i) => (
                <span
                  key={i}
                  className="text-xs px-2 py-0.5 rounded-full bg-[var(--brand-pink)]/10 text-[var(--brand-pink)] border border-[var(--brand-pink)]/20"
                >
                  {formatGenre(genre)}
                </span>
              ))}
            </div>
          )}

          {/* Top songs preview */}
          {artist.top_songs.length > 0 && (
            <div className="mt-2">
              <p className="text-[var(--text-subtle)] text-xs mb-1">Popular songs:</p>
              <div className="flex flex-wrap gap-1">
                {artist.top_songs.slice(0, 3).map((song, i) => (
                  <span
                    key={i}
                    className="text-xs px-2 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--text-muted)] truncate max-w-[120px]"
                  >
                    {song}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </button>
  );
}
