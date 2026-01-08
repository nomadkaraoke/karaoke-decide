"use client";

import { useState, useEffect, useCallback, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { ProtectedPage } from "@/components/ProtectedPage";
import {
  PlaylistIcon,
  PlusIcon,
  TrashIcon,
  MusicIcon,
  ChevronRightIcon,
  EditIcon,
  LoaderIcon,
} from "@/components/icons";
import { Button, LoadingPulse, EmptyState, Input } from "@/components/ui";
import type { Playlist } from "@/types";

function PlaylistsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");

  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [selectedPlaylist, setSelectedPlaylist] = useState<Playlist | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Create playlist modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newPlaylistName, setNewPlaylistName] = useState("");
  const [newPlaylistDescription, setNewPlaylistDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  // Edit modal state
  const [showEditModal, setShowEditModal] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  // Delete confirmation state
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [removingSongId, setRemovingSongId] = useState<string | null>(null);

  const loadPlaylists = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await api.playlists.list();
      setPlaylists(response.playlists);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load playlists"
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadPlaylistDetail = useCallback(async (playlistId: string) => {
    try {
      setIsLoadingDetail(true);
      setError(null);
      const data = await api.playlists.get(playlistId);
      setSelectedPlaylist(data);
      setEditName(data.name);
      setEditDescription(data.description || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load playlist");
      setSelectedPlaylist(null);
    } finally {
      setIsLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    loadPlaylists();
  }, [loadPlaylists]);

  useEffect(() => {
    if (selectedId) {
      loadPlaylistDetail(selectedId);
    } else {
      setSelectedPlaylist(null);
    }
  }, [selectedId, loadPlaylistDetail]);

  const handleCreatePlaylist = async () => {
    if (!newPlaylistName.trim()) return;

    try {
      setIsCreating(true);
      const newPlaylist = await api.playlists.create(
        newPlaylistName.trim(),
        newPlaylistDescription.trim() || null
      );
      setPlaylists((prev) => [newPlaylist, ...prev]);
      setShowCreateModal(false);
      setNewPlaylistName("");
      setNewPlaylistDescription("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to create playlist"
      );
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeletePlaylist = async (playlistId: string) => {
    try {
      setDeletingId(playlistId);
      await api.playlists.delete(playlistId);
      setPlaylists((prev) => prev.filter((p) => p.id !== playlistId));
      if (selectedId === playlistId) {
        router.push("/playlists");
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to delete playlist"
      );
    } finally {
      setDeletingId(null);
    }
  };

  const handleSaveEdit = async () => {
    if (!editName.trim() || !selectedPlaylist) return;

    try {
      setIsSaving(true);
      const updated = await api.playlists.update(selectedPlaylist.id, {
        name: editName.trim(),
        description: editDescription.trim() || null,
      });
      setSelectedPlaylist(updated);
      setPlaylists((prev) =>
        prev.map((p) => (p.id === updated.id ? updated : p))
      );
      setShowEditModal(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update playlist");
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemoveSong = async (songId: string) => {
    if (!selectedPlaylist) return;

    try {
      setRemovingSongId(songId);
      await api.playlists.removeSong(selectedPlaylist.id, songId);
      const updated = {
        ...selectedPlaylist,
        song_ids: selectedPlaylist.song_ids.filter((id) => id !== songId),
        song_count: selectedPlaylist.song_count - 1,
      };
      setSelectedPlaylist(updated);
      setPlaylists((prev) =>
        prev.map((p) => (p.id === updated.id ? updated : p))
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove song");
    } finally {
      setRemovingSongId(null);
    }
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  const openKaraokeSearch = (songId: string) => {
    window.open(
      `https://www.youtube.com/results?search_query=${encodeURIComponent(
        songId + " karaoke"
      )}`,
      "_blank",
      "noopener,noreferrer"
    );
  };

  // If viewing a specific playlist
  if (selectedId) {
    return (
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Back link */}
          <Link
            href="/playlists"
            className="inline-flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] mb-4 transition-colors"
          >
            <ChevronRightIcon className="w-4 h-4 rotate-180" />
            Back to Playlists
          </Link>

          {isLoadingDetail ? (
            <LoadingPulse count={5} />
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                <span className="text-2xl">!</span>
              </div>
              <p className="text-[var(--text-muted)] mb-4">{error}</p>
              <Button onClick={() => loadPlaylistDetail(selectedId)} variant="secondary">
                Try again
              </Button>
            </div>
          ) : !selectedPlaylist ? (
            <EmptyState
              icon={<PlaylistIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
              title="Playlist not found"
              description="This playlist may have been deleted."
              action={{
                label: "View All Playlists",
                onClick: () => router.push("/playlists"),
              }}
            />
          ) : (
            <>
              {/* Header */}
              <div className="mb-6">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-4">
                    <div className="w-16 h-16 rounded-xl bg-gradient-to-br from-[var(--brand-pink)]/30 to-[var(--brand-blue)]/30 flex items-center justify-center flex-shrink-0">
                      <PlaylistIcon className="w-8 h-8 text-[var(--text-muted)]" />
                    </div>
                    <div>
                      <h1 className="text-2xl font-bold text-[var(--text)]">
                        {selectedPlaylist.name}
                      </h1>
                      <p className="text-[var(--text-muted)] text-sm mt-1">
                        {selectedPlaylist.song_count} song
                        {selectedPlaylist.song_count !== 1 ? "s" : ""}
                      </p>
                      {selectedPlaylist.description && (
                        <p className="text-[var(--text-subtle)] text-sm mt-2">
                          {selectedPlaylist.description}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setShowEditModal(true)}
                      className="p-2 text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors"
                      title="Edit playlist"
                    >
                      <EditIcon className="w-5 h-5" />
                    </button>
                    <button
                      onClick={() => {
                        if (window.confirm(`Delete "${selectedPlaylist.name}"? This cannot be undone.`)) {
                          handleDeletePlaylist(selectedPlaylist.id);
                        }
                      }}
                      className="p-2 text-[var(--text-subtle)] hover:text-red-400 transition-colors"
                      title="Delete playlist"
                    >
                      <TrashIcon className="w-5 h-5" />
                    </button>
                  </div>
                </div>
              </div>

              {/* Songs */}
              {selectedPlaylist.song_ids.length === 0 ? (
                <EmptyState
                  icon={<MusicIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
                  title="No songs yet"
                  description="Add songs from your library or recommendations."
                  action={{
                    label: "Browse My Songs",
                    onClick: () => router.push("/my-songs"),
                  }}
                  secondaryAction={{
                    label: "Get Recommendations",
                    onClick: () => router.push("/recommendations"),
                  }}
                />
              ) : (
                <div className="flex flex-col gap-2">
                  {selectedPlaylist.song_ids.map((songId, index) => (
                    <div
                      key={songId}
                      className="group bg-[var(--card)] hover:bg-[var(--secondary)] rounded-xl p-3 transition-all border border-[var(--card-border)] flex items-center justify-between"
                    >
                      <div className="flex items-center gap-3 flex-1 min-w-0">
                        <span className="text-[var(--text-subtle)] text-sm w-6 text-center">
                          {index + 1}
                        </span>
                        <div className="flex-1 min-w-0">
                          <p className="text-[var(--text)] font-medium truncate">
                            Song ID: {songId}
                          </p>
                          <p className="text-[var(--text-muted)] text-sm">
                            Song details will be loaded from catalog
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => openKaraokeSearch(songId)}
                        >
                          Sing
                        </Button>
                        <button
                          onClick={() => handleRemoveSong(songId)}
                          className="p-2 text-[var(--text-subtle)] hover:text-red-400 transition-colors"
                          disabled={removingSongId === songId}
                          title="Remove from playlist"
                        >
                          {removingSongId === songId ? (
                            <span className="w-4 h-4 block animate-pulse">...</span>
                          ) : (
                            <TrashIcon className="w-4 h-4" />
                          )}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Footer navigation */}
              <div className="mt-8 pt-6 border-t border-[var(--card-border)]">
                <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
                  <Link
                    href="/my-songs"
                    className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <MusicIcon className="w-4 h-4" />
                    Add from My Songs
                  </Link>
                  <Link
                    href="/recommendations"
                    className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    Add from Recommendations
                  </Link>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Edit Modal */}
        {showEditModal && selectedPlaylist && (
          <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
            <div className="bg-[var(--card)] rounded-2xl p-6 w-full max-w-md border border-[var(--card-border)]">
              <h2 className="text-xl font-bold text-[var(--text)] mb-4">
                Edit Playlist
              </h2>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm text-[var(--text-muted)] mb-2">Name</label>
                  <Input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    placeholder="Playlist name"
                    autoFocus
                  />
                </div>

                <div>
                  <label className="block text-sm text-[var(--text-muted)] mb-2">
                    Description (optional)
                  </label>
                  <Input
                    type="text"
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    placeholder="Playlist description"
                  />
                </div>
              </div>

              <div className="flex gap-3 mt-6">
                <Button
                  variant="secondary"
                  className="flex-1"
                  onClick={() => {
                    setShowEditModal(false);
                    setEditName(selectedPlaylist.name);
                    setEditDescription(selectedPlaylist.description || "");
                  }}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  className="flex-1"
                  onClick={handleSaveEdit}
                  disabled={!editName.trim()}
                  isLoading={isSaving}
                >
                  Save
                </Button>
              </div>
            </div>
          </div>
        )}
      </main>
    );
  }

  // List view
  return (
    <main className="min-h-screen pb-safe">
      <div className="max-w-2xl mx-auto px-4 py-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
              <PlaylistIcon className="w-7 h-7 text-[var(--brand-pink)]" />
              My Playlists
            </h1>
            {playlists.length > 0 && (
              <p className="text-[var(--text-muted)] text-sm mt-1">
                {playlists.length} playlist{playlists.length !== 1 ? "s" : ""}
              </p>
            )}
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowCreateModal(true)}
          >
            <PlusIcon className="w-4 h-4" />
            New Playlist
          </Button>
        </div>

        {/* Content */}
        {isLoading ? (
          <LoadingPulse count={3} />
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
              <span className="text-2xl">!</span>
            </div>
            <p className="text-[var(--text-muted)] mb-4">{error}</p>
            <Button onClick={loadPlaylists} variant="secondary">
              Try again
            </Button>
          </div>
        ) : playlists.length === 0 ? (
          <EmptyState
            icon={<PlaylistIcon className="w-8 h-8 text-[var(--text-subtle)]" />}
            title="No playlists yet"
            description="Create your first playlist to organize your favorite karaoke songs."
            action={{
              label: "Create Playlist",
              onClick: () => setShowCreateModal(true),
            }}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {playlists.map((playlist) => (
              <div
                key={playlist.id}
                className="group bg-[var(--card)] hover:bg-[var(--secondary)] rounded-xl p-4 transition-all border border-[var(--card-border)]"
              >
                <div className="flex items-center justify-between">
                  <Link
                    href={`/playlists?id=${playlist.id}`}
                    className="flex-1 flex items-center gap-4"
                  >
                    <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-[var(--brand-pink)]/30 to-[var(--brand-blue)]/30 flex items-center justify-center">
                      <MusicIcon className="w-6 h-6 text-[var(--text-muted)]" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <h3 className="font-semibold text-[var(--text)] truncate">
                        {playlist.name}
                      </h3>
                      <p className="text-sm text-[var(--text-muted)]">
                        {playlist.song_count} song
                        {playlist.song_count !== 1 ? "s" : ""} &bull; Updated{" "}
                        {formatDate(playlist.updated_at)}
                      </p>
                      {playlist.description && (
                        <p className="text-sm text-[var(--text-subtle)] truncate mt-1">
                          {playlist.description}
                        </p>
                      )}
                    </div>
                    <ChevronRightIcon className="w-5 h-5 text-[var(--text-subtle)] group-hover:text-[var(--text-muted)] transition-colors" />
                  </Link>
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      if (
                        window.confirm(
                          `Delete "${playlist.name}"? This cannot be undone.`
                        )
                      ) {
                        handleDeletePlaylist(playlist.id);
                      }
                    }}
                    className="ml-2 p-2 text-[var(--text-subtle)] hover:text-red-400 transition-colors"
                    disabled={deletingId === playlist.id}
                  >
                    {deletingId === playlist.id ? (
                      <span className="w-5 h-5 block animate-pulse">...</span>
                    ) : (
                      <TrashIcon className="w-5 h-5" />
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Footer navigation */}
        {playlists.length > 0 && (
          <div className="mt-8 pt-6 border-t border-[var(--card-border)]">
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 text-sm">
              <Link
                href="/my-songs"
                className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
              >
                <MusicIcon className="w-4 h-4" />
                My Songs
              </Link>
              <Link
                href="/recommendations"
                className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
              >
                Discover Songs
              </Link>
            </div>
          </div>
        )}
      </div>

      {/* Create Playlist Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
          <div className="bg-[var(--card)] rounded-2xl p-6 w-full max-w-md border border-[var(--card-border)]">
            <h2 className="text-xl font-bold text-[var(--text)] mb-4">
              Create New Playlist
            </h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm text-[var(--text-muted)] mb-2">Name</label>
                <Input
                  type="text"
                  value={newPlaylistName}
                  onChange={(e) => setNewPlaylistName(e.target.value)}
                  placeholder="My Awesome Playlist"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-sm text-[var(--text-muted)] mb-2">
                  Description (optional)
                </label>
                <Input
                  type="text"
                  value={newPlaylistDescription}
                  onChange={(e) => setNewPlaylistDescription(e.target.value)}
                  placeholder="Songs for Friday night karaoke"
                />
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <Button
                variant="secondary"
                className="flex-1"
                onClick={() => {
                  setShowCreateModal(false);
                  setNewPlaylistName("");
                  setNewPlaylistDescription("");
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                className="flex-1"
                onClick={handleCreatePlaylist}
                disabled={!newPlaylistName.trim()}
                isLoading={isCreating}
              >
                Create
              </Button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

export default function PlaylistsPage() {
  return (
    <ProtectedPage>
      <Suspense
        fallback={
          <main className="min-h-screen flex items-center justify-center">
            <div className="text-center">
              <LoaderIcon className="w-10 h-10 text-[var(--brand-pink)] animate-spin mx-auto" />
              <p className="text-[var(--text-muted)] mt-4">Loading playlists...</p>
            </div>
          </main>
        }
      >
        <PlaylistsContent />
      </Suspense>
    </ProtectedPage>
  );
}
