"use client";

import { useState, useCallback, useEffect } from "react";
import { api } from "@/lib/api";
import { MusicIcon, ChevronDownIcon } from "@/components/icons";
import { Button, SourceBadge, LoadingPulse } from "@/components/ui";

interface UserSong {
  id: string;
  song_id: string;
  artist: string;
  title: string;
  source: string;
  play_count: number;
  is_saved: boolean;
  times_sung: number;
}

interface Props {
  isExpanded: boolean;
  onToggle: () => void;
  refreshTrigger?: number;
}

export function YourSongsSection({
  isExpanded,
  onToggle,
  refreshTrigger,
}: Props) {
  const [songs, setSongs] = useState<UserSong[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSongs = useCallback(
    async (pageNum: number, append: boolean = false) => {
      try {
        if (append) {
          setIsLoadingMore(true);
        } else {
          setIsLoading(true);
        }
        setError(null);

        const response = await api.my.getSongs(pageNum, 20);

        if (append) {
          setSongs((prev) => [...prev, ...(response.songs as UserSong[])]);
        } else {
          setSongs(response.songs as UserSong[]);
        }
        setTotal(response.total);
        setHasMore(response.has_more);
        setPage(pageNum);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load your songs"
        );
      } finally {
        setIsLoading(false);
        setIsLoadingMore(false);
      }
    },
    []
  );

  useEffect(() => {
    loadSongs(1);
  }, [loadSongs, refreshTrigger]);

  const handleLoadMore = () => {
    if (!isLoadingMore && hasMore) {
      loadSongs(page + 1, true);
    }
  };

  return (
    <div className="rounded-2xl bg-[rgba(20,20,30,0.9)] border border-white/10 overflow-hidden">
      {/* Header */}
      <button
        onClick={onToggle}
        className="w-full p-5 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#b347ff]/20 flex items-center justify-center">
            <MusicIcon className="w-5 h-5 text-[#b347ff]" />
          </div>
          <div>
            <h2 className="font-semibold text-white">Your Songs</h2>
            <p className="text-sm text-white/60">
              {total === 0
                ? "No songs yet"
                : `${total} song${total !== 1 ? "s" : ""} in your library`}
            </p>
          </div>
        </div>
        <ChevronDownIcon
          className={`w-5 h-5 text-white/60 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {/* Content */}
      {isExpanded && (
        <div className="px-5 pb-5 space-y-4">
          {/* Error message */}
          {error && (
            <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
              {error}
            </div>
          )}

          {isLoading ? (
            <LoadingPulse count={5} />
          ) : total === 0 ? (
            <div className="text-center py-8 text-white/40 text-sm">
              <p>No songs in your library yet.</p>
              <p className="mt-1">
                Connect your music services or take the quiz to build your library.
              </p>
            </div>
          ) : (
            <>
              {/* Song list */}
              <div className="space-y-2">
                {songs.map((song, index) => (
                  <div
                    key={song.id}
                    className="flex items-center gap-3 p-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
                  >
                    <span className="text-xs text-white/30 w-6 text-right">
                      {index + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate">
                        {song.title}
                      </p>
                      <p className="text-xs text-white/60 truncate">
                        {song.artist}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {song.play_count > 0 && (
                        <span className="text-xs text-white/40">
                          {song.play_count} plays
                        </span>
                      )}
                      <SourceBadge source={song.source as "spotify" | "lastfm" | "quiz"} />
                    </div>
                  </div>
                ))}
              </div>

              {/* Load more */}
              {hasMore && (
                <div className="text-center">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleLoadMore}
                    isLoading={isLoadingMore}
                  >
                    Load More
                  </Button>
                </div>
              )}

              {/* Summary */}
              <div className="text-center text-xs text-white/40">
                Showing {songs.length} of {total} songs
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
