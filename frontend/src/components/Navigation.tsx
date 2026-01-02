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
  LinkIcon,
  PlaylistIcon,
  SettingsIcon,
} from "./icons";
import { Button } from "./ui";

const navLinks = [
  { href: "/", label: "Search", icon: null },
  { href: "/my-songs", label: "My Songs", icon: MusicIcon, authRequired: true },
  { href: "/playlists", label: "Playlists", icon: PlaylistIcon, authRequired: true },
  { href: "/recommendations", label: "Discover", icon: SparklesIcon, authRequired: true },
  { href: "/services", label: "Services", icon: LinkIcon, authRequired: true, verifiedOnly: true },
];

export function Navigation() {
  const { user, isAuthenticated, isLoading, isGuest, isVerified, logout } = useAuth();
  const pathname = usePathname();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    setIsMobileMenuOpen(false);
    // Redirect to home
    window.location.href = "/";
  };

  const filteredLinks = navLinks.filter((link) => {
    if (link.verifiedOnly && !isVerified) return false;
    if (link.authRequired && !isAuthenticated) return false;
    return true;
  });

  return (
    <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0a0a0f]/80 border-b border-white/5">
      <div className="max-w-4xl mx-auto px-4">
        <div className="flex items-center justify-between h-16">
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
                        ? "bg-white/10 text-white"
                        : "text-white/60 hover:text-white hover:bg-white/5"
                    }
                  `}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>

          {/* Desktop Auth Section */}
          <div className="hidden md:flex items-center gap-3">
            {isLoading ? (
              <div className="w-20 h-8 rounded-lg bg-white/5 animate-pulse" />
            ) : isGuest ? (
              <div className="flex items-center gap-2">
                <span className="px-2 py-1 text-xs font-medium rounded-full bg-amber-500/20 text-amber-400">
                  Guest
                </span>
                <Link href="/login">
                  <Button variant="primary" size="sm">
                    Create Account
                  </Button>
                </Link>
                <button
                  onClick={handleLogout}
                  className="p-2 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                  title="Clear session"
                >
                  <LogOutIcon className="w-4 h-4" />
                </button>
              </div>
            ) : isAuthenticated ? (
              <div className="flex items-center gap-2">
                <Link
                  href="/profile"
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
                  title="Profile settings"
                >
                  <UserIcon className="w-4 h-4 text-white/60" />
                  <span className="text-sm text-white/80 max-w-[120px] truncate">
                    {user?.display_name || user?.email?.split("@")[0] || "User"}
                  </span>
                </Link>
                <Link
                  href="/profile"
                  className="p-2 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                  title="Settings"
                >
                  <SettingsIcon className="w-4 h-4" />
                </Link>
                <button
                  onClick={handleLogout}
                  className="p-2 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors"
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
            className="md:hidden p-2 rounded-lg text-white/60 hover:text-white hover:bg-white/10 transition-colors"
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
        <div className="md:hidden border-t border-white/5 bg-[#0a0a0f]/95 backdrop-blur-xl">
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
                        ? "bg-white/10 text-white"
                        : "text-white/60 hover:text-white hover:bg-white/5"
                    }
                  `}
                >
                  {link.icon && <link.icon className="w-5 h-5" />}
                  {link.label}
                </Link>
              );
            })}

            {/* Mobile Auth Section */}
            <div className="pt-2 border-t border-white/10">
              {isLoading ? (
                <div className="w-full h-12 rounded-xl bg-white/5 animate-pulse" />
              ) : isGuest ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-amber-500/10">
                    <UserIcon className="w-5 h-5 text-amber-400" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-white">Guest Session</p>
                        <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-amber-500/20 text-amber-400">
                          Guest
                        </span>
                      </div>
                      <p className="text-xs text-white/40">Create an account to save progress</p>
                    </div>
                  </div>
                  <Link href="/login" onClick={() => setIsMobileMenuOpen(false)}>
                    <Button variant="primary" className="w-full">
                      Create Account
                    </Button>
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="flex items-center gap-3 w-full px-4 py-3 rounded-xl text-white/40 hover:bg-white/5 transition-colors"
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
                    className="flex items-center gap-3 px-4 py-3 rounded-xl bg-white/5 hover:bg-white/10 transition-colors"
                  >
                    <UserIcon className="w-5 h-5 text-white/60" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate">
                        {user?.display_name || user?.email?.split("@")[0] || "User"}
                      </p>
                      <p className="text-xs text-white/40 truncate">{user?.email}</p>
                    </div>
                    <SettingsIcon className="w-4 h-4 text-white/40" />
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="flex items-center gap-3 w-full px-4 py-3 rounded-xl text-red-400 hover:bg-red-500/10 transition-colors"
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
