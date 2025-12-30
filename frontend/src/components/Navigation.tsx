"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import {
  MicrophoneIcon,
  UserIcon,
  LogOutIcon,
  MenuIcon,
  XIcon,
  MusicIcon,
  SparklesIcon,
  LinkIcon,
} from "./icons";
import { Button } from "./ui";

const navLinks = [
  { href: "/", label: "Search", icon: null },
  { href: "/my-songs", label: "My Songs", icon: MusicIcon, authRequired: true },
  { href: "/recommendations", label: "Discover", icon: SparklesIcon, authRequired: true },
  { href: "/services", label: "Services", icon: LinkIcon, authRequired: true },
];

export function Navigation() {
  const { user, isAuthenticated, isLoading, logout } = useAuth();
  const pathname = usePathname();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    setIsMobileMenuOpen(false);
    // Redirect to home
    window.location.href = "/";
  };

  const filteredLinks = navLinks.filter(
    (link) => !link.authRequired || isAuthenticated
  );

  return (
    <header className="sticky top-0 z-50 backdrop-blur-xl bg-[#0a0a0f]/80 border-b border-white/5">
      <div className="max-w-4xl mx-auto px-4">
        <div className="flex items-center justify-between h-16">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2">
            <div className="relative">
              <MicrophoneIcon className="w-7 h-7 text-[#ff2d92]" />
              <div className="absolute inset-0 blur-md bg-[#ff2d92]/50" />
            </div>
            <span className="text-lg font-bold">
              <span className="text-white">Nomad</span>
              <span className="text-[#ff2d92] neon-text-pink ml-1">Karaoke</span>
            </span>
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
            ) : isAuthenticated ? (
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/5">
                  <UserIcon className="w-4 h-4 text-white/60" />
                  <span className="text-sm text-white/80 max-w-[120px] truncate">
                    {user?.display_name || user?.email?.split("@")[0] || "User"}
                  </span>
                </div>
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
              ) : isAuthenticated ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-white/5">
                    <UserIcon className="w-5 h-5 text-white/60" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate">
                        {user?.display_name || user?.email?.split("@")[0] || "User"}
                      </p>
                      <p className="text-xs text-white/40 truncate">{user?.email}</p>
                    </div>
                  </div>
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
