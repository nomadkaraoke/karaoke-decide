"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { LoadingSpinner, Badge } from "@/components/ui";
import {
  ChevronLeftIcon,
  AlertCircleIcon,
  CheckIcon,
  XIcon,
  RefreshIcon,
} from "@/components/icons";

interface SyncJobDetail {
  id: string;
  user_id: string;
  user_email: string | null;
  status: string;
  created_at: string;
  completed_at: string | null;
  error: string | null;
  progress: {
    current_service: string | null;
    current_phase: string | null;
    total_tracks: number;
    processed_tracks: number;
    matched_tracks: number;
    percentage: number;
  } | null;
  results: Array<{
    service_type: string;
    tracks_fetched: number;
    tracks_matched: number;
    user_songs_created: number;
    user_songs_updated: number;
    artists_stored: number;
    error: string | null;
  }>;
}

export default function SyncJobDetailPage() {
  const searchParams = useSearchParams();
  const jobId = searchParams.get("id");

  const [job, setJob] = useState<SyncJobDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadJob = useCallback(async () => {
    if (!jobId) {
      setError("No job ID provided");
      setIsLoading(false);
      return;
    }

    try {
      setIsLoading(true);
      setError(null);
      const data = await api.admin.getSyncJob(jobId);
      setJob(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load job");
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    loadJob();
  }, [loadJob]);

  // Auto-refresh for in-progress jobs
  useEffect(() => {
    if (job?.status === "in_progress" || job?.status === "pending") {
      const interval = setInterval(loadJob, 3000);
      return () => clearInterval(interval);
    }
  }, [job?.status, loadJob]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="space-y-6">
        <Link
          href="/admin/sync-jobs"
          className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
        >
          <ChevronLeftIcon className="w-4 h-4" />
          Back to Sync Jobs
        </Link>
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-6 text-center">
          <AlertCircleIcon className="w-8 h-8 text-red-400 mx-auto mb-2" />
          <p className="text-red-400">{error || "Job not found"}</p>
        </div>
      </div>
    );
  }

  const isActive = job.status === "in_progress" || job.status === "pending";

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link
        href="/admin/sync-jobs"
        className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
      >
        <ChevronLeftIcon className="w-4 h-4" />
        Back to Sync Jobs
      </Link>

      {/* Job header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
            Sync Job
            <StatusBadge status={job.status} />
          </h2>
          <p className="text-[var(--text-muted)] mt-1 font-mono text-sm">{job.id}</p>
        </div>
        {isActive && (
          <button
            onClick={loadJob}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[var(--card)] text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
          >
            <RefreshIcon className="w-4 h-4" />
            <span className="text-sm">Refresh</span>
          </button>
        )}
      </div>

      {/* Job info */}
      <div className="grid md:grid-cols-2 gap-6">
        {/* Basic Info */}
        <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4 space-y-4">
          <h3 className="font-semibold text-[var(--text)]">Job Info</h3>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">User</span>
              <Link
                href={`/admin/users/detail?id=${job.user_id}`}
                className="text-cyan-400 hover:underline"
              >
                {job.user_email || job.user_id.slice(0, 16)}
              </Link>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">Created</span>
              <span className="text-[var(--text)]">{formatDateTime(job.created_at)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">Completed</span>
              <span className="text-[var(--text)]">
                {job.completed_at ? formatDateTime(job.completed_at) : "-"}
              </span>
            </div>
            {job.completed_at && job.created_at && (
              <div className="flex justify-between">
                <span className="text-[var(--text-muted)]">Duration</span>
                <span className="text-[var(--text)]">
                  {formatDuration(job.created_at, job.completed_at)}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Progress */}
        {job.progress && (
          <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4 space-y-4">
            <h3 className="font-semibold text-[var(--text)]">Progress</h3>
            <div className="space-y-3">
              {/* Progress bar */}
              <div>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-[var(--text-muted)]">
                    {job.progress.current_service
                      ? `${job.progress.current_service} - ${job.progress.current_phase}`
                      : "Processing..."}
                  </span>
                  <span className="text-[var(--text)]">{job.progress.percentage}%</span>
                </div>
                <div className="h-2 rounded-full bg-[var(--secondary)] overflow-hidden">
                  <div
                    className="h-full bg-cyan-400 rounded-full transition-all duration-300"
                    style={{ width: `${job.progress.percentage}%` }}
                  />
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-3 gap-4 pt-2">
                <div className="text-center">
                  <p className="text-xl font-bold text-[var(--text)]">
                    {job.progress.total_tracks}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">Total</p>
                </div>
                <div className="text-center">
                  <p className="text-xl font-bold text-[var(--text)]">
                    {job.progress.processed_tracks}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">Processed</p>
                </div>
                <div className="text-center">
                  <p className="text-xl font-bold text-green-400">
                    {job.progress.matched_tracks}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">Matched</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Error */}
      {job.error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-4">
          <h3 className="font-semibold text-red-400 mb-2">Error</h3>
          <pre className="text-sm text-red-300 whitespace-pre-wrap font-mono">
            {job.error}
          </pre>
        </div>
      )}

      {/* Results */}
      {job.results.length > 0 && (
        <section>
          <h3 className="text-lg font-semibold text-[var(--text)] mb-3">
            Service Results
          </h3>
          <div className="grid md:grid-cols-2 gap-4">
            {job.results.map((result) => (
              <div
                key={result.service_type}
                className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] p-4"
              >
                <div className="flex items-center justify-between mb-4">
                  <h4 className="font-medium text-[var(--text)] capitalize">
                    {result.service_type}
                  </h4>
                  {result.error ? (
                    <Badge variant="danger" className="flex items-center gap-1">
                      <XIcon className="w-3 h-3" />
                      Error
                    </Badge>
                  ) : (
                    <Badge variant="success" className="flex items-center gap-1">
                      <CheckIcon className="w-3 h-3" />
                      Success
                    </Badge>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <p className="text-[var(--text-muted)]">Tracks Fetched</p>
                    <p className="text-xl font-bold text-[var(--text)]">
                      {result.tracks_fetched}
                    </p>
                  </div>
                  <div>
                    <p className="text-[var(--text-muted)]">Tracks Matched</p>
                    <p className="text-xl font-bold text-green-400">
                      {result.tracks_matched}
                    </p>
                  </div>
                  <div>
                    <p className="text-[var(--text-muted)]">Songs Created</p>
                    <p className="text-lg font-semibold text-[var(--text)]">
                      {result.user_songs_created}
                    </p>
                  </div>
                  <div>
                    <p className="text-[var(--text-muted)]">Songs Updated</p>
                    <p className="text-lg font-semibold text-[var(--text)]">
                      {result.user_songs_updated}
                    </p>
                  </div>
                  <div>
                    <p className="text-[var(--text-muted)]">Artists Stored</p>
                    <p className="text-lg font-semibold text-[var(--text)]">
                      {result.artists_stored}
                    </p>
                  </div>
                  <div>
                    <p className="text-[var(--text-muted)]">Match Rate</p>
                    <p className="text-lg font-semibold text-[var(--text)]">
                      {result.tracks_fetched > 0
                        ? Math.round(
                            (result.tracks_matched / result.tracks_fetched) * 100
                          )
                        : 0}
                      %
                    </p>
                  </div>
                </div>

                {result.error && (
                  <div className="mt-4 p-2 rounded bg-red-500/10 border border-red-500/20">
                    <p className="text-xs text-red-400">{result.error}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Auto-refresh indicator */}
      {isActive && (
        <p className="text-sm text-cyan-400 text-center">
          Auto-refreshing every 3 seconds...
        </p>
      )}
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
      {status === "in_progress"
        ? "In Progress"
        : status.charAt(0).toUpperCase() + status.slice(1)}
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
    second: "2-digit",
  });
}

function formatDuration(startStr: string, endStr: string): string {
  const start = new Date(startStr).getTime();
  const end = new Date(endStr).getTime();
  const diffMs = end - start;
  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 60) {
    return `${diffSec}s`;
  } else if (diffSec < 3600) {
    const mins = Math.floor(diffSec / 60);
    const secs = diffSec % 60;
    return `${mins}m ${secs}s`;
  } else {
    const hours = Math.floor(diffSec / 3600);
    const mins = Math.floor((diffSec % 3600) / 60);
    return `${hours}h ${mins}m`;
  }
}
