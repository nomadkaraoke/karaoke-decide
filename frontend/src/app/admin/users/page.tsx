"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner, Badge } from "@/components/ui";
import {
  SearchIcon,
  ChevronRightIcon,
  AlertCircleIcon,
  ShieldIcon,
} from "@/components/icons";

interface User {
  id: string;
  email: string | null;
  display_name: string | null;
  is_guest: boolean;
  is_admin: boolean;
  created_at: string;
  last_sync_at: string | null;
  quiz_completed_at: string | null;
  total_songs_known: number;
}

type FilterType = "all" | "verified" | "guests";

export default function AdminUsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterType>("all");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(0);
  const limit = 20;

  const loadUsers = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await api.admin.listUsers({
        limit,
        offset: page * limit,
        filter,
        search: search || undefined,
      });
      setUsers(data.users);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setIsLoading(false);
    }
  }, [filter, search, page]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
    setPage(0);
  };

  const totalPages = Math.ceil(total / limit);

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-white">Users</h2>

      {/* Filters */}
      <div className="flex flex-col md:flex-row gap-4">
        {/* Search */}
        <form onSubmit={handleSearch} className="flex-1">
          <div className="relative">
            <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/40" />
            <input
              type="text"
              placeholder="Search by email..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="w-full pl-10 pr-4 py-2 rounded-lg bg-white/5 border border-white/10 text-white placeholder-white/40 focus:outline-none focus:border-white/20"
            />
          </div>
        </form>

        {/* Filter buttons */}
        <div className="flex gap-2">
          {(["all", "verified", "guests"] as FilterType[]).map((f) => (
            <button
              key={f}
              onClick={() => {
                setFilter(f);
                setPage(0);
              }}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                filter === f
                  ? "bg-white/10 text-white"
                  : "bg-white/5 text-white/60 hover:text-white hover:bg-white/10"
              }`}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Results count */}
      <p className="text-sm text-white/60">
        {total} user{total !== 1 ? "s" : ""} found
      </p>

      {/* Error state */}
      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 p-4 text-center">
          <AlertCircleIcon className="w-6 h-6 text-red-400 mx-auto mb-2" />
          <p className="text-red-400">{error}</p>
          <button
            onClick={loadUsers}
            className="mt-3 px-4 py-2 rounded-lg bg-white/10 text-white hover:bg-white/20 transition-colors"
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

      {/* Users list */}
      {!isLoading && !error && (
        <div className="rounded-xl bg-white/5 border border-white/10 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/10">
                <th className="px-4 py-3 text-left text-sm font-medium text-white/60">
                  User
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-white/60">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-white/60 hidden md:table-cell">
                  Created
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-white/60 hidden md:table-cell">
                  Last Sync
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-white/60 hidden lg:table-cell">
                  Songs
                </th>
                <th className="px-4 py-3 w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {users.map((user) => (
                <tr
                  key={user.id}
                  className="hover:bg-white/5 transition-colors"
                >
                  <td className="px-4 py-3">
                    <div>
                      <p className="text-white font-medium truncate max-w-[200px]">
                        {user.display_name || user.email || "No name"}
                      </p>
                      <p className="text-sm text-white/40 truncate max-w-[200px]">
                        {user.email || user.id.slice(0, 8)}
                      </p>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {user.is_admin && (
                        <Badge variant="primary" className="flex items-center gap-1">
                          <ShieldIcon className="w-3 h-3" />
                          Admin
                        </Badge>
                      )}
                      {user.is_guest ? (
                        <Badge variant="warning">Guest</Badge>
                      ) : (
                        <Badge variant="success">Verified</Badge>
                      )}
                      {user.quiz_completed_at && (
                        <Badge variant="secondary">Quiz</Badge>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-white/60 hidden md:table-cell">
                    {formatDate(user.created_at)}
                  </td>
                  <td className="px-4 py-3 text-sm text-white/60 hidden md:table-cell">
                    {user.last_sync_at ? formatDate(user.last_sync_at) : "Never"}
                  </td>
                  <td className="px-4 py-3 text-sm text-white/60 hidden lg:table-cell">
                    {user.total_songs_known}
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/users/detail?id=${user.id}`}
                      className="p-2 rounded-lg text-white/40 hover:text-white hover:bg-white/10 transition-colors inline-block"
                    >
                      <ChevronRightIcon className="w-4 h-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {users.length === 0 && (
            <div className="p-8 text-center text-white/40">
              No users found matching your criteria.
            </div>
          )}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-white/60">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-4 py-2 rounded-lg bg-white/5 text-white disabled:opacity-50 disabled:cursor-not-allowed hover:bg-white/10 transition-colors"
            >
              Previous
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-4 py-2 rounded-lg bg-white/5 text-white disabled:opacity-50 disabled:cursor-not-allowed hover:bg-white/10 transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function formatDate(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) {
    return "Today";
  } else if (diffDays === 1) {
    return "Yesterday";
  } else if (diffDays < 7) {
    return `${diffDays}d ago`;
  } else {
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  }
}
