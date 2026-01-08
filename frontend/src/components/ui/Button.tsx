"use client";

import { forwardRef, ButtonHTMLAttributes, ReactNode } from "react";
import { LoadingSpinner } from "./LoadingSpinner";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "primary",
      size = "md",
      isLoading = false,
      leftIcon,
      rightIcon,
      className = "",
      disabled,
      children,
      ...props
    },
    ref
  ) => {
    const baseClasses =
      "inline-flex items-center justify-center font-semibold transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-pink)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg)] disabled:opacity-50 disabled:cursor-not-allowed";

    const variantClasses = {
      primary:
        "bg-[var(--brand-pink)] text-white hover:bg-[var(--brand-pink-hover)] hover:scale-105 hover:shadow-[0_0_20px_rgba(255,122,204,0.5)] active:scale-95",
      secondary:
        "bg-[var(--secondary)] text-[var(--text)] border border-[var(--card-border)] hover:bg-[var(--secondary-hover)] hover:border-[var(--text-subtle)] active:bg-[var(--secondary)]",
      ghost:
        "text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--secondary)] active:bg-[var(--secondary-hover)]",
      danger:
        "bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 active:bg-red-500/20",
    };

    const sizeClasses = {
      sm: "px-3 py-1.5 text-sm rounded-lg gap-1.5",
      md: "px-4 py-2 text-sm rounded-xl gap-2",
      lg: "px-6 py-3 text-base rounded-2xl gap-2.5",
    };

    return (
      <button
        ref={ref}
        className={`${baseClasses} ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
        disabled={disabled || isLoading}
        {...props}
      >
        {isLoading ? (
          <LoadingSpinner size="sm" />
        ) : (
          <>
            {leftIcon && <span className="flex-shrink-0">{leftIcon}</span>}
            {children}
            {rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
          </>
        )}
      </button>
    );
  }
);

Button.displayName = "Button";
