"use client";

import { ArrowRightIcon, ChevronRightIcon } from "@/components/icons";

interface StickyFinishBarProps {
  selectedCount: number;
  onFinish: () => void;
  onSkip: () => void;
  onBack?: () => void;
  isSubmitting: boolean;
}

/**
 * Sticky bar at the bottom of the viewport showing selection count and finish button.
 * Always visible so users can finish the quiz at any time while scrolling.
 */
export function StickyFinishBar({
  selectedCount,
  onFinish,
  onSkip,
  onBack,
  isSubmitting,
}: StickyFinishBarProps) {
  const hasSelections = selectedCount > 0;

  return (
    <div
      className="fixed bottom-0 left-0 right-0 z-50 border-t border-[var(--card-border)]"
      style={{
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        backgroundColor: "rgba(var(--background-rgb), 0.85)",
      }}
    >
      <div className="max-w-3xl mx-auto px-4 py-4">
        <div className="flex items-center justify-between gap-4">
          {/* Left side: Back button + Selection count */}
          <div className="flex items-center gap-3">
            {/* Back button */}
            {onBack && (
              <button
                data-testid="sticky-back-button"
                onClick={onBack}
                aria-label="Back"
                className="flex items-center gap-1 px-3 py-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] transition-colors text-sm"
              >
                <ChevronRightIcon className="w-4 h-4 transform rotate-180" aria-hidden="true" />
                <span className="hidden sm:inline">Back</span>
              </button>
            )}

            {/* Selection count */}
            <div className="flex items-center gap-2">
              {hasSelections ? (
                <>
                  <span className="flex items-center justify-center w-6 h-6 rounded-full bg-[var(--brand-pink)] text-sm font-semibold">
                    {selectedCount}
                  </span>
                  <span className="text-[var(--text)] text-sm sm:text-base hidden sm:inline">
                    {selectedCount === 1 ? "artist" : "artists"} selected
                  </span>
                </>
              ) : (
                <span className="text-[var(--text-muted)] text-sm sm:text-base hidden sm:inline">
                  Select artists you know
                </span>
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex items-center gap-3">
            {/* Skip link - always visible */}
            <button
              onClick={onSkip}
              disabled={isSubmitting}
              className="text-[var(--text-muted)] hover:text-[var(--text)] text-sm transition-colors disabled:opacity-50"
            >
              Skip
            </button>

            {/* Finish button */}
            <button
              onClick={onFinish}
              disabled={isSubmitting}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-lg font-medium text-sm sm:text-base
                transition-all duration-200
                ${
                  hasSelections
                    ? "bg-[var(--brand-pink)] text-white hover:opacity-90"
                    : "bg-[var(--secondary)] text-[var(--text-muted)] hover:bg-[var(--card)]"
                }
                disabled:opacity-50 disabled:cursor-not-allowed
              `}
            >
              {isSubmitting ? (
                <>
                  <span className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  Finish Quiz
                  <ArrowRightIcon className="w-4 h-4" />
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
