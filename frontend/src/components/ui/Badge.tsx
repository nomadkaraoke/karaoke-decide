"use client";

import { ReactNode } from "react";

interface BadgeProps {
  variant?: "default" | "spotify" | "lastfm" | "quiz" | "success" | "warning" | "error";
  size?: "sm" | "md";
  children: ReactNode;
  className?: string;
}

export function Badge({
  variant = "default",
  size = "sm",
  children,
  className = "",
}: BadgeProps) {
  const variantClasses = {
    default: "bg-white/10 text-white/70 border-white/20",
    spotify: "bg-[#1DB954]/20 text-[#1DB954] border-[#1DB954]/30",
    lastfm: "bg-[#D51007]/20 text-[#ff4444] border-[#D51007]/30",
    quiz: "bg-[#b347ff]/20 text-[#b347ff] border-[#b347ff]/30",
    success: "bg-green-500/20 text-green-400 border-green-500/30",
    warning: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    error: "bg-red-500/20 text-red-400 border-red-500/30",
  };

  const sizeClasses = {
    sm: "px-2 py-0.5 text-xs",
    md: "px-2.5 py-1 text-sm",
  };

  return (
    <span
      className={`
        inline-flex items-center gap-1 rounded-full border font-medium
        ${variantClasses[variant]}
        ${sizeClasses[size]}
        ${className}
      `}
    >
      {children}
    </span>
  );
}

// Convenience component for source badges
export function SourceBadge({ source }: { source: string }) {
  const variant = source === "spotify" ? "spotify" : source === "lastfm" ? "lastfm" : "quiz";
  const label = source === "spotify" ? "Spotify" : source === "lastfm" ? "Last.fm" : "Quiz";

  return <Badge variant={variant}>{label}</Badge>;
}
