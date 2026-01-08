"use client";

import { useState, useEffect, FormEvent, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { ProtectedPage } from "@/components/ProtectedPage";
import {
  SettingsIcon,
  UserIcon,
  MusicIcon,
  SpotifyIcon,
  LastfmIcon,
  LogOutIcon,
  CheckIcon,
  ChevronRightIcon,
  TrashIcon,
} from "@/components/icons";
import { Button, Input, Badge, LoadingPulse } from "@/components/ui";

interface Preferences {
  decade: string | null;
  energy: string | null;
  genres: string[];
}

interface ServiceInfo {
  connected: boolean;
  username?: string;
}

export default function SettingsPage() {
  const { user, isGuest, checkAuth, logout } = useAuth();

  // Profile state
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileSuccess, setProfileSuccess] = useState<string | null>(null);

  // Preferences state
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [prefsLoading, setPrefsLoading] = useState(true);

  // Services state
  const [services, setServices] = useState<Record<string, ServiceInfo>>({});
  const [servicesLoading, setServicesLoading] = useState(true);

  // Danger zone state
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Sync display name when user data loads
  useEffect(() => {
    if (user?.display_name) {
      setDisplayName(user.display_name);
    }
  }, [user?.display_name]);

  // Load preferences and services
  const loadData = useCallback(async () => {
    try {
      setPrefsLoading(true);
      setServicesLoading(true);

      const summary = await api.my.getDataSummary();

      setPreferences({
        decade: summary.preferences.decade || null,
        energy: summary.preferences.energy || null,
        genres: summary.preferences.genres || [],
      });

      setServices(summary.services);
    } catch (err) {
      console.error("Failed to load data:", err);
    } finally {
      setPrefsLoading(false);
      setServicesLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleProfileSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setProfileError(null);
    setProfileSuccess(null);

    try {
      setIsSubmitting(true);
      await api.auth.updateProfile({
        display_name: displayName.trim() || null,
      });
      await checkAuth();
      setProfileSuccess("Profile updated!");
    } catch (err) {
      setProfileError(err instanceof Error ? err.message : "Failed to update profile");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    window.location.href = "/";
  };

  // Delete account functionality - placeholder for future implementation
  const handleDeleteAccount = async () => {
    // API endpoint not implemented yet
    setDeleteError("Account deletion is not yet available. Please contact support if you need to delete your account.");
  };

  const hasProfileChanges = (displayName.trim() || null) !== (user?.display_name || null);

  const connectedServicesCount = Object.values(services).filter(s => s.connected).length;

  return (
    <ProtectedPage>
      <main className="min-h-screen pb-safe">
        <div className="max-w-2xl mx-auto px-4 py-6">
          {/* Header */}
          <div className="mb-8">
            <h1 className="text-2xl font-bold text-[var(--text)] flex items-center gap-3">
              <SettingsIcon className="w-7 h-7 text-[var(--brand-blue)]" />
              Settings
            </h1>
            <p className="text-[var(--text-muted)] text-sm mt-1">
              Manage your profile and preferences
            </p>
          </div>

          <div className="space-y-6">
            {/* Profile Section */}
            <section className="p-5 rounded-2xl bg-[var(--card)] border border-[var(--card-border)]">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-[var(--brand-pink)]/20 flex items-center justify-center">
                  <UserIcon className="w-5 h-5 text-[var(--brand-pink)]" />
                </div>
                <div>
                  <h2 className="font-semibold text-[var(--text)]">Profile</h2>
                  <p className="text-xs text-[var(--text-muted)]">Your account information</p>
                </div>
              </div>

              {/* Error/Success messages */}
              {profileError && (
                <div className="mb-4 p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                  {profileError}
                </div>
              )}
              {profileSuccess && (
                <div className="mb-4 p-3 rounded-xl bg-green-500/10 border border-green-500/30 text-green-400 text-sm flex items-center gap-2">
                  <CheckIcon className="w-4 h-4" />
                  {profileSuccess}
                </div>
              )}

              <form onSubmit={handleProfileSubmit} className="space-y-4">
                {/* Email (read-only) */}
                <div>
                  <label className="block text-xs font-medium text-[var(--text-muted)] mb-1.5">
                    Email
                  </label>
                  <div className="px-3 py-2.5 rounded-lg bg-[var(--card)] border border-[var(--card-border)] text-[var(--text-muted)] text-sm">
                    {user?.email || "Guest session"}
                  </div>
                </div>

                {/* Display name */}
                <div>
                  <label htmlFor="displayName" className="block text-xs font-medium text-[var(--text-muted)] mb-1.5">
                    Display Name
                  </label>
                  <Input
                    id="displayName"
                    type="text"
                    placeholder="How should we call you?"
                    value={displayName}
                    onChange={(e) => {
                      setDisplayName(e.target.value);
                      setProfileSuccess(null);
                    }}
                    maxLength={50}
                  />
                </div>

                {/* Save button */}
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  isLoading={isSubmitting}
                  disabled={!hasProfileChanges}
                >
                  Save Changes
                </Button>
              </form>
            </section>

            {/* Preferences Section */}
            <section className="p-5 rounded-2xl bg-[var(--card)] border border-[var(--card-border)]">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-[var(--brand-blue)]/20 flex items-center justify-center">
                    <MusicIcon className="w-5 h-5 text-[var(--brand-blue)]" />
                  </div>
                  <div>
                    <h2 className="font-semibold text-[var(--text)]">Music Preferences</h2>
                    <p className="text-xs text-[var(--text-muted)]">Your karaoke style</p>
                  </div>
                </div>
                <Link href="/quiz">
                  <Button variant="secondary" size="sm">
                    Update
                  </Button>
                </Link>
              </div>

              {prefsLoading ? (
                <LoadingPulse count={2} />
              ) : preferences ? (
                <div className="space-y-3">
                  {/* Genres */}
                  <div className="flex flex-wrap gap-2">
                    {preferences.genres.length > 0 ? (
                      preferences.genres.map((genre) => (
                        <Badge key={genre} variant="default">
                          {genre}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-sm text-[var(--text-subtle)]">No genres selected</span>
                    )}
                  </div>

                  {/* Decade & Energy */}
                  <div className="flex items-center gap-3 text-sm">
                    {preferences.decade ? (
                      <span className="px-2.5 py-1 rounded-full bg-[var(--secondary)] text-[var(--text-muted)]">
                        {preferences.decade}
                      </span>
                    ) : null}
                    {preferences.energy ? (
                      <span className="px-2.5 py-1 rounded-full bg-[var(--secondary)] text-[var(--text-muted)]">
                        {preferences.energy} energy
                      </span>
                    ) : null}
                    {!preferences.decade && !preferences.energy && preferences.genres.length === 0 && (
                      <span className="text-[var(--text-subtle)]">
                        Take the quiz to set your preferences
                      </span>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-[var(--text-subtle)]">
                  Take the quiz to set your preferences
                </p>
              )}
            </section>

            {/* Connected Services Quick View */}
            <Link href="/music-i-know" className="block">
              <section className="p-5 rounded-2xl bg-[var(--card)] border border-[var(--card-border)] hover:border-[var(--card-border)] transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    {/* Service icons */}
                    <div className="flex items-center gap-1">
                      <div className={`w-9 h-9 rounded-full flex items-center justify-center ${
                        services.spotify?.connected ? "bg-[#1DB954]/20" : "bg-[var(--secondary)]"
                      }`}>
                        <SpotifyIcon className={`w-4 h-4 ${
                          services.spotify?.connected ? "text-[#1DB954]" : "text-[var(--text-subtle)]"
                        }`} />
                      </div>
                      <div className={`w-9 h-9 rounded-full flex items-center justify-center ${
                        services.lastfm?.connected ? "bg-[#D51007]/20" : "bg-[var(--secondary)]"
                      }`}>
                        <LastfmIcon className={`w-4 h-4 ${
                          services.lastfm?.connected ? "text-[#ff4444]" : "text-[var(--text-subtle)]"
                        }`} />
                      </div>
                    </div>
                    <div>
                      <h2 className="font-semibold text-[var(--text)]">Connected Services</h2>
                      <p className="text-xs text-[var(--text-muted)]">
                        {servicesLoading ? "Loading..." :
                          connectedServicesCount === 0 ? "No services connected" :
                          `${connectedServicesCount} service${connectedServicesCount !== 1 ? "s" : ""} connected`
                        }
                      </p>
                    </div>
                  </div>
                  <ChevronRightIcon className="w-5 h-5 text-[var(--text-subtle)]" />
                </div>
              </section>
            </Link>

            {/* Log Out */}
            <section className="p-5 rounded-2xl bg-[var(--card)] border border-[var(--card-border)]">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-[var(--secondary)] flex items-center justify-center">
                    <LogOutIcon className="w-5 h-5 text-[var(--text-muted)]" />
                  </div>
                  <div>
                    <h2 className="font-semibold text-[var(--text)]">Log Out</h2>
                    <p className="text-xs text-[var(--text-muted)]">
                      {isGuest ? "Clear your guest session" : "Sign out of your account"}
                    </p>
                  </div>
                </div>
                <Button variant="secondary" size="sm" onClick={handleLogout}>
                  {isGuest ? "Clear Session" : "Log Out"}
                </Button>
              </div>
            </section>

            {/* Danger Zone - Only for non-guests */}
            {!isGuest && (
              <section className="p-5 rounded-2xl bg-red-500/5 border border-red-500/20">
                <div className="flex items-center gap-3 mb-4">
                  <div className="w-10 h-10 rounded-full bg-red-500/20 flex items-center justify-center">
                    <TrashIcon className="w-5 h-5 text-red-400" />
                  </div>
                  <div>
                    <h2 className="font-semibold text-red-400">Danger Zone</h2>
                    <p className="text-xs text-[var(--text-muted)]">Irreversible actions</p>
                  </div>
                </div>

                {deleteError && (
                  <div className="mb-4 p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                    {deleteError}
                  </div>
                )}

                {!showDeleteConfirm ? (
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => setShowDeleteConfirm(true)}
                  >
                    Delete Account
                  </Button>
                ) : (
                  <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/30">
                    <p className="text-sm text-[var(--text)] mb-4">
                      Are you sure? This will permanently delete your account and all your data.
                      This action cannot be undone.
                    </p>
                    <div className="flex items-center gap-3">
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={handleDeleteAccount}
                      >
                        Yes, Delete My Account
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setShowDeleteConfirm(false)}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </section>
            )}

            {/* Account ID */}
            <div className="text-center text-xs text-[var(--text-subtle)]">
              Account ID: {user?.id}
            </div>
          </div>
        </div>
      </main>
    </ProtectedPage>
  );
}
