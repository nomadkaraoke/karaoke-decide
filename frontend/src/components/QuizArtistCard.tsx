"use client";

import { MicrophoneIcon } from "@/components/icons";

interface SuggestionReason {
  type: "fans_also_like" | "similar_artist" | "genre_match" | "decade_match" | "popular_choice";
  display_text: string;
  related_to: string | null;
}

/**
 * Quiz artist with MBID-first identifiers.
 * MBID is the primary identifier when available.
 */
interface QuizArtist {
  // Primary identifier (MusicBrainz)
  mbid?: string | null;
  name: string;

  // Karaoke catalog data
  song_count: number;
  top_songs: string[];
  total_brand_count: number;
  primary_decade: string;

  // Enrichment (optional)
  spotify_id?: string | null;
  genres?: string[];
  tags?: string[];
  image_url: string | null;
  suggestion_reason?: SuggestionReason | null;
}

/**
 * Affinity levels for artist selection:
 * - occasionally: Listen to sometimes (weight: 1x)
 * - like: Generally enjoy (weight: 2x)
 * - love: Absolutely love (weight: 3x)
 */
export type ArtistAffinity = "occasionally" | "like" | "love";

interface QuizArtistCardProps {
  artist: QuizArtist;
  affinity: ArtistAffinity | null;
  onAffinityChange: (affinity: ArtistAffinity | null) => void;
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

/**
 * Get color classes for suggestion reason type
 */
function getReasonColors(type: SuggestionReason["type"]): string {
  switch (type) {
    case "fans_also_like":
      // Purple for collaborative recommendations - most personal/relevant
      return "bg-[var(--brand-purple)]/15 text-[var(--brand-purple)] border-[var(--brand-purple)]/30";
    case "similar_artist":
      return "bg-[var(--brand-pink)]/15 text-[var(--brand-pink)] border-[var(--brand-pink)]/30";
    case "genre_match":
      return "bg-[var(--brand-blue)]/15 text-[var(--brand-blue)] border-[var(--brand-blue)]/30";
    case "decade_match":
      return "bg-[var(--brand-yellow)]/15 text-[var(--brand-yellow)] border-[var(--brand-yellow)]/30";
    case "popular_choice":
    default:
      return "bg-[var(--text)]/10 text-[var(--text-muted)] border-[var(--text)]/20";
  }
}

/**
 * Badge showing why an artist was suggested
 */
function ReasonBadge({ reason }: { reason: SuggestionReason }) {
  return (
    <span
      className={`
        inline-flex items-center text-xs px-2 py-0.5 rounded-full border
        ${getReasonColors(reason.type)}
      `}
      title={reason.related_to ? `Similar to ${reason.related_to}` : undefined}
    >
      {reason.display_text}
    </span>
  );
}

const AFFINITY_OPTIONS: Array<{
  value: ArtistAffinity;
  label: string;
  emoji: string;
  description: string;
}> = [
  { value: "occasionally", label: "Occasionally", emoji: "🎵", description: "Listen sometimes" },
  { value: "like", label: "Like", emoji: "❤️", description: "Generally enjoy" },
  { value: "love", label: "LOVE", emoji: "🔥", description: "Absolutely love" },
];

export function QuizArtistCard({
  artist,
  affinity,
  onAffinityChange,
  index,
}: QuizArtistCardProps) {
  const displayGenres = artist.genres?.slice(0, 3) || [];
  const hasAffinity = affinity !== null;

  const handleAffinityClick = (value: ArtistAffinity) => {
    // Toggle: if clicking the same level, deselect; otherwise set new level
    if (affinity === value) {
      onAffinityChange(null);
    } else {
      onAffinityChange(value);
    }
  };

  return (
    <div
      data-testid={`artist-card-${index}`}
      className={`
        relative w-full p-4 rounded-xl text-left transition-all duration-200
        ${
          hasAffinity
            ? "bg-gradient-to-r from-[var(--brand-pink)]/20 to-[var(--brand-purple)]/20 border-[var(--brand-pink)]/50 border-2"
            : "bg-[var(--secondary)] border border-[var(--card-border)]"
        }
      `}
      style={{
        animationDelay: `${index * 50}ms`,
      }}
    >
      <div className="flex items-start gap-4">
        {/* Artist avatar placeholder */}
        <div
          className={`
            w-12 h-12 rounded-full flex items-center justify-center flex-shrink-0
            ${hasAffinity ? "bg-[var(--brand-pink)]/30" : "bg-[var(--secondary)]"}
          `}
        >
          <MicrophoneIcon
            className={`w-5 h-5 ${hasAffinity ? "text-[var(--brand-pink)]" : "text-[var(--text-muted)]"}`}
          />
        </div>

        {/* Artist info */}
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-[var(--text)] truncate text-lg">
            {artist.name}
          </h3>
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-[var(--text-muted)] text-sm">
              {artist.song_count} karaoke songs
            </p>
            {/* Suggestion reason badge */}
            {artist.suggestion_reason && (
              <ReasonBadge reason={artist.suggestion_reason} />
            )}
          </div>

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

          {/* Affinity buttons */}
          <div className="flex gap-2 mt-3">
            {AFFINITY_OPTIONS.map((option) => {
              const isActive = affinity === option.value;
              return (
                <button
                  key={option.value}
                  onClick={() => handleAffinityClick(option.value)}
                  title={option.description}
                  className={`
                    flex-1 py-2 px-2 rounded-lg text-center transition-all duration-200
                    ${
                      isActive
                        ? "bg-[var(--brand-pink)] text-white shadow-sm scale-[1.02]"
                        : "bg-[var(--card)] border border-[var(--card-border)] text-[var(--text-muted)] hover:border-[var(--brand-pink)]/50 hover:text-[var(--text)]"
                    }
                  `}
                >
                  <span className="text-base block">{option.emoji}</span>
                  <span className="text-xs font-medium block mt-0.5">{option.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
