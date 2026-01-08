"use client";

import { LoaderIcon } from "../icons";

interface LoadingSpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function LoadingSpinner({ size = "md", className = "" }: LoadingSpinnerProps) {
  const sizeClasses = {
    sm: "w-4 h-4",
    md: "w-6 h-6",
    lg: "w-8 h-8",
  };

  return (
    <LoaderIcon
      className={`animate-spin text-[var(--brand-pink)] ${sizeClasses[size]} ${className}`}
    />
  );
}

interface LoadingPulseProps {
  count?: number;
  className?: string;
}

export function LoadingPulse({ count = 4, className = "" }: LoadingPulseProps) {
  return (
    <div className={`flex flex-col gap-4 ${className}`}>
      {[...Array(count)].map((_, i) => (
        <div
          key={i}
          className="h-28 rounded-2xl bg-[var(--secondary)] animate-pulse"
          style={{ animationDelay: `${i * 100}ms` }}
        />
      ))}
    </div>
  );
}

interface LoadingOverlayProps {
  message?: string;
}

export function LoadingOverlay({ message = "Loading..." }: LoadingOverlayProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--bg)]/80 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-4">
        <LoadingSpinner size="lg" />
        <p className="text-[var(--text-muted)] text-sm">{message}</p>
      </div>
    </div>
  );
}
