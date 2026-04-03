"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { LoadingSpinner, Badge } from "@/components/ui";
import {
  ChevronLeftIcon,
  AlertCircleIcon,
  SpotifyIcon,
  LastfmIcon,
  ShieldIcon,
  CheckIcon,
  XIcon,
  TrashIcon,
} from "@/components/icons";

interface UserDetail {
  id: string;
  email: string | null;
  display_name: string | null;
  is_guest: boolean;
  is_admin: boolean;
  created_at: string;
  last_sync_at: string | null;
  quiz_completed_at: string | null;
  total_songs_known: number;
  services: Array<{
    service_type: string;
    service_username: string;
    sync_status: string;
    last_sync_at: string | null;
    tracks_synced: number;
    sync_error: string | null;
  }>;
  sync_jobs: Array<{
    id: string;
    status: string;
    created_at: string;
    completed_at: string | null;
    error: string | null;
  }>;
  data_summary: {
    artists_count: number;
    songs_count: number;
    playlists_count: number;
  };
}

export default function UserDetailPage() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const userId = searchParams.get("id");

  const [user, setUser] = useState<UserDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const handleDelete = async () => {
    if (!userId || !user) return;

    setIsDeleting(true);
    setDeleteError(null);

    try {
      await api.admin.deleteUser(userId);
      router.push("/admin/users");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Failed to delete user");
      setIsDeleting(false);
    }
  };

  useEffect(() => {
    const loadUser = async () => {
      if (!userId) {
        setError("No user ID provided");
        setIsLoading(false);
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const data = await api.admin.getUser(userId);
        setUser(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load user");
      } finally {
        setIsLoading(false);
      }
    };

    loadUser();
  }, [userId]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error || !user) {
    return (
      <div className="space-y-6">
        <Link
          href="/admin/users"
          className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
        >
          <ChevronLeftIcon className="w-4 h-4" />
          Back to Users
        </Link>
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-6 text-center">
          <AlertCircleIcon className="w-8 h-8 text-red-400 mx-auto mb-2" />
          <p className="text-red-400">{error || "User not found"}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        href="/admin/users"
        className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
      >
        <ChevronLeftIcon className="w-4 h-4" />
        Back to Users
      </Link>

      {/* User header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
            {user.display_name || user.email || "Unknown User"}
            {user.is_admin && (
              <Badge variant="primary" className="flex items-center gap-1">
                <ShieldIcon className="w-3 h-3" />
                Admin
              </Badge>
            )}
          </h2>
          <p className="text-[var(--text-muted)] mt-1">{user.email || user.id}</p>
        </div>
        <div className="flex items-center gap-3">
          {user.is_guest ? (
            <Badge variant="warning">Guest</Badge>
          ) : (
            <Badge variant="success">Verified</Badge>
          )}
          {!user.is_admin && (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
            >
              <TrashIcon className="w-4 h-4" />
              Delete
            </button>
          )}
        </div>
      </div>

      {/* Delete confirmation modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-[var(--text)] mb-2">
              Delete User?
            </h3>
            <p className="text-[var(--text-muted)] mb-4">
              This will permanently delete <strong>{user.email || user.id}</strong> and all their data including:
            </p>
            <ul className="text-sm text-[var(--text-muted)] mb-4 list-disc list-inside space-y-1">
              <li>{user.data_summary.songs_count} songs</li>
              <li>{user.data_summary.artists_count} artists</li>
              <li>{user.data_summary.playlists_count} playlists</li>
              <li>All connected services and sync jobs</li>
            </ul>
            <p className="text-red-400 text-sm mb-4">
              This action cannot be undone.
            </p>
            {deleteError && (
              <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                {deleteError}
              </div>
            )}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                disabled={isDeleting}
                className="px-4 py-2 rounded-lg bg-[var(--secondary)] text-[var(--text)] hover:bg-[var(--card-border)] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {isDeleting ? (
                  <>
                    <LoadingSpinner size="sm" />
                    Deleting...
                  </>
                ) : (
                  <>
                    <TrashIcon className="w-4 h-4" />
                    Delete User
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* User info grid */}
      <div className="grid md:grid-cols-2 gap-6">
        {/* Basic Info */}
        <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4 space-y-4">
          <h3 className="font-semibold text-[var(--text)]">Account Info</h3>
          <div className="space-y-3 text-sm">
            <InfoRow label="User ID" value={user.id} mono />
            <InfoRow
              label="Created"
              value={formatDateTime(user.created_at)}
            />
            <InfoRow
              label="Last Sync"
              value={user.last_sync_at ? formatDateTime(user.last_sync_at) : "Never"}
            />
            <InfoRow
              label="Quiz Completed"
              value={
                user.quiz_completed_at
                  ? formatDateTime(user.quiz_completed_at)
                  : "No"
              }
            />
          </div>
        </div>

        {/* Data Summary */}
        <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4 space-y-4">
          <h3 className="font-semibold text-[var(--text)]">Data Summary</h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <p className="text-2xl font-bold text-[var(--text)]">
                {user.data_summary.artists_count}
              </p>
              <p className="text-sm text-[var(--text-muted)]">Artists</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-[var(--text)]">
                {user.data_summary.songs_count}
              </p>
              <p className="text-sm text-[var(--text-muted)]">Songs</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-[var(--text)]">
                {user.data_summary.playlists_count}
              </p>
              <p className="text-sm text-[var(--text-muted)]">Playlists</p>
            </div>
          </div>
        </div>
      </div>

      {/* Connected Services */}
      <section>
        <h3 className="text-lg font-semibold text-[var(--text)] mb-3">
          Connected Services
        </h3>
        {user.services.length === 0 ? (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-6 text-center text-[var(--text-subtle)]">
            No services connected
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-4">
            {user.services.map((service) => (
              <div
                key={service.service_type}
                className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4"
              >
                <div className="flex items-center gap-3 mb-3">
                  {service.service_type === "spotify" ? (
                    <SpotifyIcon className="w-6 h-6 text-green-400" />
                  ) : (
                    <LastfmIcon className="w-6 h-6 text-red-400" />
                  )}
                  <div>
                    <p className="font-medium text-[var(--text)] capitalize">
                      {service.service_type}
                    </p>
                    <p className="text-sm text-[var(--text-muted)]">
                      @{service.service_username}
                    </p>
                  </div>
                </div>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">Status</span>
                    <Badge
                      variant={
                        service.sync_status === "error"
                          ? "danger"
                          : service.sync_status === "syncing"
                          ? "warning"
                          : "secondary"
                      }
                    >
                      {service.sync_status}
                    </Badge>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">Tracks Synced</span>
                    <span className="text-[var(--text)]">{service.tracks_synced}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">Last Sync</span>
                    <span className="text-[var(--text)]">
                      {service.last_sync_at
                        ? formatDateTime(service.last_sync_at)
                        : "Never"}
                    </span>
                  </div>
                  {service.sync_error && (
                    <div className="mt-2 p-2 rounded bg-red-500/10 border border-red-500/20">
                      <p className="text-xs text-red-400">{service.sync_error}</p>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Recent Sync Jobs */}
      <section>
        <h3 className="text-lg font-semibold text-[var(--text)] mb-3">
          Recent Sync Jobs
        </h3>
        {user.sync_jobs.length === 0 ? (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-6 text-center text-[var(--text-subtle)]">
            No sync jobs found
          </div>
        ) : (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--card-border)]">
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    Job ID
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    Created
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    Completed
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {user.sync_jobs.map((job) => (
                  <tr key={job.id} className="hover:bg-[var(--card)] transition-colors">
                    <td className="px-4 py-3">
                      <Link
                        href={`/admin/sync-jobs/detail?id=${job.id}`}
                        className="text-cyan-400 hover:underline font-mono text-sm"
                      >
                        {job.id.slice(0, 8)}...
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={job.status} />
                    </td>
                    <td className="px-4 py-3 text-sm text-[var(--text-muted)]">
                      {formatDateTime(job.created_at)}
                    </td>
                    <td className="px-4 py-3 text-sm text-[var(--text-muted)]">
                      {job.completed_at
                        ? formatDateTime(job.completed_at)
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function InfoRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-[var(--text-muted)]">{label}</span>
      <span className={`text-[var(--text)] ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const variants: Record<string, "success" | "warning" | "danger" | "secondary"> = {
    completed: "success",
    pending: "warning",
    in_progress: "warning",
    failed: "danger",
  };

  const icons: Record<string, React.ReactNode> = {
    completed: <CheckIcon className="w-3 h-3" />,
    failed: <XIcon className="w-3 h-3" />,
  };

  return (
    <Badge
      variant={variants[status] || "secondary"}
      className="flex items-center gap-1"
    >
      {icons[status]}
      {status.replace("_", " ")}
    </Badge>
  );
}

function formatDateTime(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
