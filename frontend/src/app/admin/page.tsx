"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/ui";
import {
  UsersIcon,
  ActivityIcon,
  SpotifyIcon,
  LastfmIcon,
  AlertCircleIcon,
  CheckIcon,
  RefreshIcon,
} from "@/components/icons";

interface Stats {
  users: {
    total: number;
    verified: number;
    guests: number;
    active_7d: number;
  };
  sync_jobs: {
    total: number;
    pending: number;
    in_progress: number;
    completed: number;
    failed: number;
  };
  services: {
    spotify_connected: number;
    lastfm_connected: number;
  };
}

export default function AdminDashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadStats = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await api.admin.getStats();
      setStats(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load stats");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadStats();
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-6 text-center">
        <AlertCircleIcon className="w-8 h-8 text-red-400 mx-auto mb-2" />
        <p className="text-red-400">{error}</p>
        <button
          onClick={loadStats}
          className="mt-4 px-4 py-2 rounded-lg bg-white/10 text-white hover:bg-white/20 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!stats) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-white">Dashboard</h2>
        <button
          onClick={loadStats}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 text-white/60 hover:text-white hover:bg-white/10 transition-colors"
        >
          <RefreshIcon className="w-4 h-4" />
          <span className="text-sm">Refresh</span>
        </button>
      </div>

      {/* User Stats */}
      <section>
        <h3 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
          <UsersIcon className="w-5 h-5 text-cyan-400" />
          Users
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Total Users" value={stats.users.total} />
          <StatCard
            label="Verified"
            value={stats.users.verified}
            color="green"
          />
          <StatCard label="Guests" value={stats.users.guests} color="amber" />
          <StatCard
            label="Active (7d)"
            value={stats.users.active_7d}
            color="cyan"
          />
        </div>
        <Link
          href="/admin/users"
          className="inline-block mt-3 text-sm text-white/60 hover:text-white transition-colors"
        >
          View all users &rarr;
        </Link>
      </section>

      {/* Sync Jobs Stats (24h) */}
      <section>
        <h3 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
          <ActivityIcon className="w-5 h-5 text-purple-400" />
          Sync Jobs (24h)
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <StatCard label="Total" value={stats.sync_jobs.total} />
          <StatCard
            label="Pending"
            value={stats.sync_jobs.pending}
            color="amber"
          />
          <StatCard
            label="In Progress"
            value={stats.sync_jobs.in_progress}
            color="cyan"
          />
          <StatCard
            label="Completed"
            value={stats.sync_jobs.completed}
            color="green"
            icon={<CheckIcon className="w-4 h-4" />}
          />
          <StatCard
            label="Failed"
            value={stats.sync_jobs.failed}
            color="red"
            icon={<AlertCircleIcon className="w-4 h-4" />}
          />
        </div>
        <Link
          href="/admin/sync-jobs"
          className="inline-block mt-3 text-sm text-white/60 hover:text-white transition-colors"
        >
          View all sync jobs &rarr;
        </Link>
      </section>

      {/* Service Connections */}
      <section>
        <h3 className="text-lg font-semibold text-white mb-3">
          Service Connections
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-xl bg-white/5 border border-white/10 p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-green-500/20">
                <SpotifyIcon className="w-5 h-5 text-green-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">
                  {stats.services.spotify_connected}
                </p>
                <p className="text-sm text-white/60">Spotify Connected</p>
              </div>
            </div>
          </div>
          <div className="rounded-xl bg-white/5 border border-white/10 p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-red-500/20">
                <LastfmIcon className="w-5 h-5 text-red-400" />
              </div>
              <div>
                <p className="text-2xl font-bold text-white">
                  {stats.services.lastfm_connected}
                </p>
                <p className="text-sm text-white/60">Last.fm Connected</p>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  color = "default",
  icon,
}: {
  label: string;
  value: number;
  color?: "default" | "green" | "amber" | "cyan" | "red" | "purple";
  icon?: React.ReactNode;
}) {
  const colorClasses = {
    default: "text-white",
    green: "text-green-400",
    amber: "text-amber-400",
    cyan: "text-cyan-400",
    red: "text-red-400",
    purple: "text-purple-400",
  };

  return (
    <div className="rounded-xl bg-white/5 border border-white/10 p-4">
      <div className="flex items-center justify-between">
        <p className={`text-2xl font-bold ${colorClasses[color]}`}>{value}</p>
        {icon && <span className={colorClasses[color]}>{icon}</span>}
      </div>
      <p className="text-sm text-white/60 mt-1">{label}</p>
    </div>
  );
}
