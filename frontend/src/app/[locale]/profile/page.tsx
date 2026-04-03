"use client";

import { useState, useEffect, FormEvent } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { ProtectedPage } from "@/components/ProtectedPage";
import { UserIcon, CheckIcon } from "@/components/icons";
import { Button, Input } from "@/components/ui";

export default function ProfilePage() {
  const { user, checkAuth } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Sync display name when user data loads
  useEffect(() => {
    if (user?.display_name) {
      setDisplayName(user.display_name);
    }
  }, [user?.display_name]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMessage(null);

    try {
      setIsSubmitting(true);
      await api.auth.updateProfile({
        display_name: displayName.trim() || null,
      });
      await checkAuth();
      setSuccessMessage("Profile updated successfully!");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update profile");
    } finally {
      setIsSubmitting(false);
    }
  };

  const hasChanges = (displayName.trim() || null) !== (user?.display_name || null);

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
              <UserIcon className="w-7 h-7 text-[var(--brand-pink)]" />
              Profile Settings
            </h1>
            <p className="text-[var(--text-muted)] text-sm mt-1">
              Manage your account settings and display name
            </p>
          </div>

          {/* Error message */}
          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400">
              {error}
            </div>
          )}

          {/* Success message */}
          {successMessage && (
            <div className="mb-6 p-4 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400 flex items-center gap-2">
              <CheckIcon className="w-5 h-5" />
              {successMessage}
            </div>
          )}

          {/* Profile form */}
          <div className="p-5 rounded-2xl bg-[var(--card)] border border-[var(--card-border)]">
            <form onSubmit={handleSubmit} className="space-y-6">
              {/* Account info (read-only) */}
              <div>
                <label className="block text-sm font-medium text-[var(--text)] mb-2">
                  Email Address
                </label>
                <div className="px-4 py-3 rounded-xl bg-[var(--card)] border border-[var(--card-border)] text-[var(--text-muted)]">
                  {user?.email}
                </div>
                <p className="text-xs text-[var(--text-subtle)] mt-1">
                  Email cannot be changed
                </p>
              </div>

              {/* Display name (editable) */}
              <div>
                <label
                  htmlFor="displayName"
                  className="block text-sm font-medium text-[var(--text)] mb-2"
                >
                  Display Name
                </label>
                <Input
                  id="displayName"
                  type="text"
                  placeholder="Enter your display name"
                  value={displayName}
                  onChange={(e) => {
                    setDisplayName(e.target.value);
                    setSuccessMessage(null);
                  }}
                  maxLength={50}
                />
                <p className="text-xs text-[var(--text-subtle)] mt-1">
                  This is how your name will appear in the app
                </p>
              </div>

              {/* Submit button */}
              <div className="pt-2">
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={isSubmitting}
                  disabled={!hasChanges}
                  className="w-full sm:w-auto"
                >
                  Save Changes
                </Button>
              </div>
            </form>
          </div>

          {/* Account ID info */}
          <div className="mt-6 p-4 rounded-xl bg-[var(--card)] border border-[var(--card-border)]">
            <p className="text-xs text-[var(--text-subtle)]">
              Account ID: <span className="font-mono">{user?.id}</span>
            </p>
          </div>
        </div>
      </main>
    </ProtectedPage>
  );
}
