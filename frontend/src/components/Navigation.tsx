"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  UserIcon,
  LogOutIcon,
  MenuIcon,
  XIcon,
  MusicIcon,
  SparklesIcon,
  PlaylistIcon,
  SettingsIcon,
  ShieldIcon,
} from "./icons";
import { Button } from "./ui";
import { ThemeToggle } from "./ThemeToggle";

const navLinks = [
  { href: "/recommendations", label: "Recommendations", icon: SparklesIcon, authRequired: true },
  { href: "/music-i-know", label: "Music I Know", icon: MusicIcon, authRequired: true },
  { href: "/playlists", label: "Playlists", icon: PlaylistIcon, authRequired: true },
  { href: "/settings", label: "Settings", icon: SettingsIcon, authRequired: true },
];

export function Navigation() {
  const { user, isAuthenticated, isLoading, isGuest, isAdmin, logout } = useAuth();
  const pathname = usePathname();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    setIsMobileMenuOpen(false);
    // Redirect to home
    window.location.href = "/";
  };

  const filteredLinks = navLinks.filter((link) => {
    if (link.authRequired && !isAuthenticated) return false;
    return true;
  });

  return (
    <header className="fixed top-0 left-0 right-0 z-50 backdrop-blur-md border-b border-[var(--card-border)]">
      <div className="max-w-6xl mx-auto px-4 py-4">
        <div className="flex items-center justify-between">
          {/* Logo */}
          <Link href="/" className="flex items-center">
            <Image
              src="/nomad-karaoke-logo.svg"
              alt="Nomad Karaoke"
              width={140}
              height={50}
              priority
              className="h-10 w-auto"
            />
          </Link>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center gap-1">
            {filteredLinks.map((link) => {
              const isActive = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`
                    px-4 py-2 rounded-lg text-sm font-medium transition-colors
                    ${
                      isActive
                        ? "bg-[var(--secondary)] text-[var(--text)]"
                        : "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)]"
                    }
                  `}
                >
                  {link.label}
                </Link>
              );
            })}
            {isAdmin && (
              <Link
                href="/admin"
                className={`
                  px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5
                  ${
                    pathname.startsWith("/admin")
                      ? "bg-[var(--brand-purple)]/20 text-[var(--brand-purple)]"
                      : "text-[var(--brand-purple)]/60 hover:text-[var(--brand-purple)] hover:bg-[var(--brand-purple)]/10"
                  }
                `}
              >
                <ShieldIcon className="w-4 h-4" />
                Admin
              </Link>
            )}
          </nav>

          {/* Desktop Auth Section */}
          <div className="hidden md:flex items-center gap-3">
            <ThemeToggle />
            {isLoading ? (
              <div className="w-20 h-8 rounded-lg bg-[var(--secondary)] animate-pulse" />
            ) : isGuest ? (
              <div className="flex items-center gap-2">
                <span className="px-2 py-1 text-xs font-medium rounded-full bg-[var(--warning)]/20 text-[var(--warning)]">
                  Guest
                </span>
                <Link href="/login">
                  <Button variant="primary" size="sm">
                    Create Account
                  </Button>
                </Link>
                <button
                  onClick={handleLogout}
                  className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
                  title="Clear session"
                >
                  <LogOutIcon className="w-4 h-4" />
                </button>
              </div>
            ) : isAuthenticated ? (
              <div className="flex items-center gap-2">
                <Link
                  href="/profile"
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[var(--secondary)] hover:bg-[var(--secondary)]/80 transition-colors"
                  title="Profile settings"
                >
                  <UserIcon className="w-4 h-4 text-[var(--text-muted)]" />
                  <span className="text-sm text-[var(--text-muted)] max-w-[120px] truncate">
                    {user?.display_name || user?.email?.split("@")[0] || "User"}
                  </span>
                </Link>
                <Link
                  href="/profile"
                  className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
                  title="Settings"
                >
                  <SettingsIcon className="w-4 h-4" />
                </Link>
                <button
                  onClick={handleLogout}
                  className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
                  title="Log out"
                >
                  <LogOutIcon className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <Link href="/login">
                <Button variant="primary" size="sm">
                  Sign In
                </Button>
              </Link>
            )}
          </div>

          {/* Mobile Menu Button */}
          <button
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            className="md:hidden p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors"
            aria-label={isMobileMenuOpen ? "Close menu" : "Open menu"}
            aria-expanded={isMobileMenuOpen}
          >
            {isMobileMenuOpen ? (
              <XIcon className="w-6 h-6" />
            ) : (
              <MenuIcon className="w-6 h-6" />
            )}
          </button>
        </div>
      </div>

      {/* Mobile Menu */}
      {isMobileMenuOpen && (
        <div className="md:hidden border-t border-[var(--card-border)] bg-[var(--bg)]/95 backdrop-blur-xl">
          <div className="px-4 py-4 space-y-2">
            {filteredLinks.map((link) => {
              const isActive = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={() => setIsMobileMenuOpen(false)}
                  className={`
                    flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-colors
                    ${
                      isActive
                        ? "bg-[var(--secondary)] text-[var(--text)]"
                        : "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)]"
                    }
                  `}
                >
                  {link.icon && <link.icon className="w-5 h-5" />}
                  {link.label}
                </Link>
              );
            })}
            {isAdmin && (
              <Link
                href="/admin"
                onClick={() => setIsMobileMenuOpen(false)}
                className={`
                  flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium transition-colors
                  ${
                    pathname.startsWith("/admin")
                      ? "bg-[var(--brand-purple)]/20 text-[var(--brand-purple)]"
                      : "text-[var(--brand-purple)]/60 hover:text-[var(--brand-purple)] hover:bg-[var(--brand-purple)]/10"
                  }
                `}
              >
                <ShieldIcon className="w-5 h-5" />
                Admin
              </Link>
            )}

            {/* Mobile Theme Toggle */}
            <div className="flex items-center justify-between px-4 py-3 rounded-xl bg-[var(--secondary)]">
              <span className="text-sm font-medium text-[var(--text)]">Theme</span>
              <ThemeToggle />
            </div>

            {/* Mobile Auth Section */}
            <div className="pt-2 border-t border-[var(--card-border)]">
              {isLoading ? (
                <div className="w-full h-12 rounded-xl bg-[var(--secondary)] animate-pulse" />
              ) : isGuest ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[var(--warning)]/10">
                    <UserIcon className="w-5 h-5 text-[var(--warning)]" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-[var(--text)]">Guest Session</p>
                        <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-[var(--warning)]/20 text-[var(--warning)]">
                          Guest
                        </span>
                      </div>
                      <p className="text-xs text-[var(--text-subtle)]">Create an account to save progress</p>
                    </div>
                  </div>
                  <Link href="/login" onClick={() => setIsMobileMenuOpen(false)}>
                    <Button variant="primary" className="w-full">
                      Create Account
                    </Button>
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="flex items-center gap-3 w-full px-4 py-3 rounded-xl text-[var(--text-subtle)] hover:bg-[var(--secondary)] transition-colors"
                  >
                    <LogOutIcon className="w-5 h-5" />
                    <span className="text-sm font-medium">Clear Session</span>
                  </button>
                </div>
              ) : isAuthenticated ? (
                <div className="space-y-2">
                  <Link
                    href="/profile"
                    onClick={() => setIsMobileMenuOpen(false)}
                    className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[var(--secondary)] hover:bg-[var(--secondary)]/80 transition-colors"
                  >
                    <UserIcon className="w-5 h-5 text-[var(--text-muted)]" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-[var(--text)] truncate">
                        {user?.display_name || user?.email?.split("@")[0] || "User"}
                      </p>
                      <p className="text-xs text-[var(--text-subtle)] truncate">{user?.email}</p>
                    </div>
                    <SettingsIcon className="w-4 h-4 text-[var(--text-subtle)]" />
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="flex items-center gap-3 w-full px-4 py-3 rounded-xl text-[var(--error)] hover:bg-[var(--error)]/10 transition-colors"
                  >
                    <LogOutIcon className="w-5 h-5" />
                    <span className="text-sm font-medium">Log out</span>
                  </button>
                </div>
              ) : (
                <Link href="/login" onClick={() => setIsMobileMenuOpen(false)}>
                  <Button variant="primary" className="w-full">
                    Sign In
                  </Button>
                </Link>
              )}
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
