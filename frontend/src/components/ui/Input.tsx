"use client";

import { forwardRef, InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, helperText, className = "", id, ...props }, ref) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col gap-1.5">
        {label && (
          <label
            htmlFor={inputId}
            className="text-sm font-medium text-[var(--text-muted)]"
          >
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={`
            w-full px-4 py-3 rounded-xl
            bg-[var(--bg)] border
            text-[var(--text)] placeholder-[var(--text-subtle)]
            transition-all duration-200
            focus:outline-none focus:ring-2 focus:ring-[var(--brand-pink)]/50 focus:border-[var(--brand-pink)]
            ${
              error
                ? "border-red-500/50 focus:border-red-500"
                : "border-[var(--card-border)] hover:border-[var(--text-subtle)]"
            }
            ${className}
          `}
          {...props}
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        {helperText && !error && (
          <p className="text-sm text-[var(--text-subtle)]">{helperText}</p>
        )}
      </div>
    );
  }
);

Input.displayName = "Input";
