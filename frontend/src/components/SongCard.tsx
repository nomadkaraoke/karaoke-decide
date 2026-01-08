"use client";

import { useEffect, useRef, useState } from "react";
import {
  ChevronDownIcon,
  MicrophoneIcon,
  StarIcon,
  VideoIcon,
  YouTubeIcon,
} from "./icons";

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
          className={`w-3.5 h-3.5 ${i < stars ? "text-[var(--brand-gold)]" : "text-[var(--text-subtle)]"}`}
        />
      ))}
      <span className="ml-1.5 text-xs text-[var(--text-subtle)] font-mono">{count}</span>
    </div>
  );
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

export function SongCard({ song, index = 0, showAnimation = true }: SongCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const links = getKaraokeLinks(song.artist, song.title);

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
        className={`absolute -inset-0.5 rounded-2xl bg-gradient-to-r from-[var(--brand-blue)] via-[var(--brand-purple)] to-[var(--brand-pink)] opacity-0 blur-sm transition-opacity duration-300 ${
          isHovered ? "opacity-60" : ""
        }`}
      />

      <div className="relative flex flex-col gap-3 p-4 rounded-2xl bg-[var(--card)] border border-[var(--card-border)] backdrop-blur-sm transition-all duration-300 hover:border-[var(--text-subtle)]">
        {/* Song info */}
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold text-[var(--text)] truncate group-hover:text-[var(--brand-pink)] transition-colors">
            {song.title}
          </h3>
          <p className="text-sm text-[var(--text-muted)] truncate mt-0.5">{song.artist}</p>
        </div>

        {/* Bottom row */}
        <div className="flex items-center justify-between gap-3">
          <PopularityStars count={song.brandCount} />

          {/* Karaoke button with dropdown */}
          <div className="relative" ref={dropdownRef}>
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
          </div>
        </div>
      </div>
    </div>
  );
}
