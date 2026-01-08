"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner, Badge } from "@/components/ui";
import {
  ChevronRightIcon,
  AlertCircleIcon,
  CheckIcon,
  XIcon,
  RefreshIcon,
} from "@/components/icons";

interface SyncJob {
  id: string;
  user_id: string;
  user_email: string | null;
  status: string;
  created_at: string;
  completed_at: string | null;
  error: string | null;
}

type StatusFilter = "all" | "pending" | "in_progress" | "completed" | "failed";

export default function AdminSyncJobsPage() {
  const [jobs, setJobs] = useState<SyncJob[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [page, setPage] = useState(0);
  const limit = 20;

  const loadJobs = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await api.admin.listSyncJobs({
        limit,
        offset: page * limit,
        status: statusFilter,
      });
      setJobs(data.jobs);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sync jobs");
    } finally {
      setIsLoading(false);
    }
  }, [statusFilter, page]);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // Auto-refresh for in-progress jobs
  useEffect(() => {
    if (statusFilter === "in_progress" || statusFilter === "pending") {
      const interval = setInterval(loadJobs, 5000);
      return () => clearInterval(interval);
    }
  }, [statusFilter, loadJobs]);

  const totalPages = Math.ceil(total / limit);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-[var(--text)]">Sync Jobs</h2>
        <button
          onClick={loadJobs}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[var(--card)] text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
        >
          <RefreshIcon className="w-4 h-4" />
          <span className="text-sm">Refresh</span>
        </button>
      </div>

      {/* Status filter */}
      <div className="flex flex-wrap gap-2">
        {(
          ["all", "pending", "in_progress", "completed", "failed"] as StatusFilter[]
        ).map((status) => (
          <button
            key={status}
            onClick={() => {
              setStatusFilter(status);
              setPage(0);
            }}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              statusFilter === status
                ? "bg-[var(--secondary)] text-[var(--text)]"
                : "bg-[var(--card)] text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)]"
            }`}
          >
            {status === "in_progress"
              ? "In Progress"
              : status.charAt(0).toUpperCase() + status.slice(1)}
          </button>
        ))}
      </div>

      {/* Results count */}
      <p className="text-sm text-[var(--text-muted)]">
        {total} job{total !== 1 ? "s" : ""} found
        {(statusFilter === "in_progress" || statusFilter === "pending") && (
          <span className="ml-2 text-cyan-400">(auto-refreshing)</span>
        )}
      </p>

      {/* Error state */}
      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-4 text-center">
          <AlertCircleIcon className="w-6 h-6 text-red-400 mx-auto mb-2" />
          <p className="text-red-400">{error}</p>
          <button
            onClick={loadJobs}
            className="mt-3 px-4 py-2 rounded-lg bg-[var(--secondary)] text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center h-32">
          <LoadingSpinner size="lg" />
        </div>
      )}

      {/* Jobs list */}
      {!isLoading && !error && (
        <div className="rounded-xl bg-[var(--card)] border border-[var(--card-border)] overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[var(--card-border)]">
                <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                  Job ID
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                  User
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)]">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)] hidden md:table-cell">
                  Created
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-[var(--text-muted)] hidden md:table-cell">
                  Completed
                </th>
                <th className="px-4 py-3 w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {jobs.map((job) => (
                <tr
                  key={job.id}
                  className="hover:bg-[var(--card)] transition-colors"
                >
                  <td className="px-4 py-3">
                    <code className="text-sm text-[var(--text)] font-mono">
                      {job.id.slice(0, 8)}...
                    </code>
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/users/detail?id=${job.user_id}`}
                      className="text-cyan-400 hover:underline text-sm"
                    >
                      {job.user_email || job.user_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 text-sm text-[var(--text-muted)] hidden md:table-cell">
                    {formatDateTime(job.created_at)}
                  </td>
                  <td className="px-4 py-3 text-sm text-[var(--text-muted)] hidden md:table-cell">
                    {job.completed_at ? formatDateTime(job.completed_at) : "-"}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/sync-jobs/detail?id=${job.id}`}
                      className="p-2 rounded-lg text-[var(--text-subtle)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors inline-block"
                    >
                      <ChevronRightIcon className="w-4 h-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {jobs.length === 0 && (
            <div className="p-8 text-center text-[var(--text-subtle)]">
              No sync jobs found matching your criteria.
            </div>
          )}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-[var(--text-muted)]">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-4 py-2 rounded-lg bg-[var(--card)] text-[var(--text)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--secondary)] transition-colors"
            >
              Previous
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-4 py-2 rounded-lg bg-[var(--card)] text-[var(--text)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--secondary)] transition-colors"
            >
              Next
            </button>
          </div>
        </div>
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
      {status === "in_progress" ? "In Progress" : status.charAt(0).toUpperCase() + status.slice(1)}
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
