"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
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
  const userId = searchParams.get("id");

  const [user, setUser] = useState<UserDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
          className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
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
        className="flex items-center gap-2 text-white/60 hover:text-white transition-colors"
      >
        <ChevronLeftIcon className="w-4 h-4" />
        Back to Users
      </Link>

      {/* User header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white flex items-center gap-3">
            {user.display_name || user.email || "Unknown User"}
            {user.is_admin && (
              <Badge variant="primary" className="flex items-center gap-1">
                <ShieldIcon className="w-3 h-3" />
                Admin
              </Badge>
            )}
          </h2>
          <p className="text-white/60 mt-1">{user.email || user.id}</p>
        </div>
        <div className="flex items-center gap-2">
          {user.is_guest ? (
            <Badge variant="warning">Guest</Badge>
          ) : (
            <Badge variant="success">Verified</Badge>
          )}
        </div>
      </div>

      {/* User info grid */}
      <div className="grid md:grid-cols-2 gap-6">
        {/* Basic Info */}
        <div className="rounded-xl bg-white/5 border border-white/10 p-4 space-y-4">
          <h3 className="font-semibold text-white">Account Info</h3>
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
        <div className="rounded-xl bg-white/5 border border-white/10 p-4 space-y-4">
          <h3 className="font-semibold text-white">Data Summary</h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center">
              <p className="text-2xl font-bold text-white">
                {user.data_summary.artists_count}
              </p>
              <p className="text-sm text-white/60">Artists</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-white">
                {user.data_summary.songs_count}
              </p>
              <p className="text-sm text-white/60">Songs</p>
            </div>
            <div className="text-center">
              <p className="text-2xl font-bold text-white">
                {user.data_summary.playlists_count}
              </p>
              <p className="text-sm text-white/60">Playlists</p>
            </div>
          </div>
        </div>
      </div>

      {/* Connected Services */}
      <section>
        <h3 className="text-lg font-semibold text-white mb-3">
          Connected Services
        </h3>
        {user.services.length === 0 ? (
          <div className="rounded-xl bg-white/5 border border-white/10 p-6 text-center text-white/40">
            No services connected
          </div>
        ) : (
          <div className="grid md:grid-cols-2 gap-4">
            {user.services.map((service) => (
              <div
                key={service.service_type}
                className="rounded-xl bg-white/5 border border-white/10 p-4"
              >
                <div className="flex items-center gap-3 mb-3">
                  {service.service_type === "spotify" ? (
                    <SpotifyIcon className="w-6 h-6 text-green-400" />
                  ) : (
                    <LastfmIcon className="w-6 h-6 text-red-400" />
                  )}
                  <div>
                    <p className="font-medium text-white capitalize">
                      {service.service_type}
                    </p>
                    <p className="text-sm text-white/60">
                      @{service.service_username}
                    </p>
                  </div>
                </div>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-white/60">Status</span>
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
                    <span className="text-white/60">Tracks Synced</span>
                    <span className="text-white">{service.tracks_synced}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-white/60">Last Sync</span>
                    <span className="text-white">
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
        <h3 className="text-lg font-semibold text-white mb-3">
          Recent Sync Jobs
        </h3>
        {user.sync_jobs.length === 0 ? (
          <div className="rounded-xl bg-white/5 border border-white/10 p-6 text-center text-white/40">
            No sync jobs found
          </div>
        ) : (
          <div className="rounded-xl bg-white/5 border border-white/10 overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="px-4 py-3 text-left text-sm font-medium text-white/60">
                    Job ID
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-white/60">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-white/60">
                    Created
                  </th>
                  <th className="px-4 py-3 text-left text-sm font-medium text-white/60">
                    Completed
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {user.sync_jobs.map((job) => (
                  <tr key={job.id} className="hover:bg-white/5 transition-colors">
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
                    <td className="px-4 py-3 text-sm text-white/60">
                      {formatDateTime(job.created_at)}
                    </td>
                    <td className="px-4 py-3 text-sm text-white/60">
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
      <span className="text-white/60">{label}</span>
      <span className={`text-white ${mono ? "font-mono text-xs" : ""}`}>
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
