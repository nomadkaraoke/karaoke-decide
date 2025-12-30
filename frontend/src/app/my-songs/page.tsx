"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { ProtectedPage } from "@/components/ProtectedPage";
import { UserSongCard } from "@/components/UserSongCard";
import { MusicIcon, LinkIcon, SparklesIcon } from "@/components/icons";
import { Button, LoadingPulse, EmptyState } from "@/components/ui";

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

export default function MySongsPage() {
  const [songs, setSongs] = useState<UserSong[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSongs = useCallback(async (pageNum: number, append: boolean = false) => {
    try {
      if (append) {
        setIsLoadingMore(true);
      } else {
        setIsLoading(true);
      }
      setError(null);

      const response = await api.my.getSongs(pageNum, 20);

      if (append) {
        setSongs((prev) => [...prev, ...response.songs as UserSong[]]);
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
  }, []);

  useEffect(() => {
    loadSongs(1);
  }, [loadSongs]);

  const handleLoadMore = () => {
    if (!isLoadingMore && hasMore) {
      loadSongs(page + 1, true);
    }
  };

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <div>
              <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                <MusicIcon className="w-7 h-7 text-[#ff2d92]" />
                My Songs
              </h1>
              {total > 0 && (
                <p className="text-white/60 text-sm mt-1">
                  {total} songs in your library
                </p>
              )}
            </div>
            <Link href="/recommendations">
              <Button variant="secondary" size="sm">
                <SparklesIcon className="w-4 h-4" />
                Get Recommendations
              </Button>
            </Link>
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
              <Button onClick={() => loadSongs(1)} variant="secondary">
                Try again
              </Button>
            </div>
          ) : songs.length === 0 ? (
            <EmptyState
              icon={<MusicIcon className="w-8 h-8 text-white/20" />}
              title="No songs yet"
              description="Connect your music services or take the quiz to build your song library."
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
              {/* Song list */}
              <div className="flex flex-col gap-3">
                {songs.map((song, index) => (
                  <UserSongCard key={song.id} song={song} index={index} />
                ))}
              </div>

              {/* Load more */}
              {hasMore && (
                <div className="mt-6 text-center">
                  <Button
                    variant="secondary"
                    onClick={handleLoadMore}
                    isLoading={isLoadingMore}
                  >
                    Load More
                  </Button>
                </div>
              )}

              {/* Footer */}
              <div className="mt-8 pt-6 border-t border-white/10">
                <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
                  <Link
                    href="/services"
                    className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
                  >
                    <LinkIcon className="w-4 h-4" />
                    Sync more music
                  </Link>
                  <Link
                    href="/quiz"
                    className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
                  >
                    <SparklesIcon className="w-4 h-4" />
                    Update preferences
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
