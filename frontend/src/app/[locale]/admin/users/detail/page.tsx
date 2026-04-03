"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
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
      setDeleteError(err instanceof Error ? err.message : t("failedToDeleteUser"));
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
        setError(err instanceof Error ? err.message : t("failedToLoadUser"));
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
          {t("backToUsers")}
        </Link>
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-6 text-center">
          <AlertCircleIcon className="w-8 h-8 text-red-400 mx-auto mb-2" />
          <p className="text-red-400">{error || t("userNotFound")}</p>
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
        {t("backToUsers")}
      </Link>

      {/* User header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
            {user.display_name || user.email || t("unknownUser")}
            {user.is_admin && (
              <Badge variant="primary" className="flex items-center gap-1">
                <ShieldIcon className="w-3 h-3" />
                {t("admin")}
              </Badge>
            )}
          </h2>
          <p className="text-[var(--text-muted)] mt-1">{user.email || user.id}</p>
        </div>
        <div className="flex items-center gap-3">
          {user.is_guest ? (
            <Badge variant="warning">{t("guest")}</Badge>
          ) : (
            <Badge variant="success">{t("verified")}</Badge>
          )}
          {!user.is_admin && (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
            >
              <TrashIcon className="w-4 h-4" />
              {t("delete")}
            </button>
          )}
        </div>
      </div>

      {/* Delete confirmation modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold text-[var(--text)] mb-2">
              {t("deleteUserConfirm")}
            </h3>
            <p className="text-[var(--text-muted)] mb-4">
              {t("deleteUserWillRemove", { identifier: user.email || user.id })}
            </p>
            <ul className="text-sm text-[var(--text-muted)] mb-4 list-disc list-inside space-y-1">
              <li>{t("songs", { count: user.data_summary.songs_count })}</li>
              <li>{t("artists", { count: user.data_summary.artists_count })}</li>
              <li>{t("playlistsData", { count: user.data_summary.playlists_count })}</li>
              <li>{t("allServicesAndSyncJobs")}</li>
            </ul>
            <p className="text-red-400 text-sm mb-4">
              {t("cannotBeUndone")}
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
                {tCommon("cancel")}
              </button>
              <button
                onClick={handleDelete}
                disabled={isDeleting}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {isDeleting ? (
                  <>
                    <LoadingSpinner size="sm" />
                    {t("deleting")}
                  </>
                ) : (
                  <>
                    <TrashIcon className="w-4 h-4" />
                    {t("deleteUser")}
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
          <h3 className="font-semibold text-[var(--text)]">{t("accountInfo")}</h3>
          <div className="space-y-3 text-sm">
            <InfoRow label={t("userId")} value={user.id} mono />
            <InfoRow
              label={t("created")}
              value={formatDateTime(user.created_at)}
            />
            <InfoRow
              label={t("lastSync")}
              value={user.last_sync_at ? formatDateTime(user.last_sync_at) : t("never")}
            />
            <InfoRow
              label={t("quizCompleted")}
              value={
                user.quiz_completed_at
                  ? formatDateTime(user.quiz_completed_at)
                  : t("no")
              }
            />
          </div>
        </div>

        {/* Data Summary */}
        <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4 space-y-4">
          <h3 className="font-semibold text-[var(--text)]">{t("dataSummary")}</h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <p className="text-2xl font-bold text-[var(--text)]">
                {user.data_summary.artists_count}
              </p>
              <p className="text-sm text-[var(--text-muted)]">{t("artists", { count: user.data_summary.artists_count })}</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-[var(--text)]">
                {user.data_summary.songs_count}
              </p>
              <p className="text-sm text-[var(--text-muted)]">{t("songs", { count: user.data_summary.songs_count })}</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-[var(--text)]">
                {user.data_summary.playlists_count}
              </p>
              <p className="text-sm text-[var(--text-muted)]">{t("playlistsData", { count: user.data_summary.playlists_count })}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Connected Services */}
      <section>
        <h3 className="text-lg font-semibold text-[var(--text)] mb-3">
          {t("connectedServicesSection")}
        </h3>
        {user.services.length === 0 ? (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-6 text-center text-[var(--text-subtle)]">
            {t("noServicesConnected")}
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
                    <span className="text-[var(--text-muted)]">{t("status")}</span>
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
                    <span className="text-[var(--text-muted)]">{t("tracksSynced")}</span>
                    <span className="text-[var(--text)]">{service.tracks_synced}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t("lastSync")}</span>
                    <span className="text-[var(--text)]">
                      {service.last_sync_at
                        ? formatDateTime(service.last_sync_at)
                        : t("never")}
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
          {t("recentSyncJobs")}
        </h3>
        {user.sync_jobs.length === 0 ? (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-6 text-center text-[var(--text-subtle)]">
            {t("noSyncJobsFound")}
          </div>
        ) : (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--card-border)]">
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    {t("jobId")}
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    {t("status")}
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    {t("created")}
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                    {t("completed")}
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
                      <StatusBadge status={job.status} t={t} />
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

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
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

  const statusLabels: Record<string, string> = {
    completed: t("completed"),
    pending: t("pending"),
    in_progress: t("inProgress"),
    failed: t("failed"),
  };

  return (
    <Badge
      variant={variants[status] || "secondary"}
      className="flex items-center gap-1"
    >
      {icons[status]}
      {statusLabels[status] || status.replace("_", " ")}
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
