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
            className="text-sm font-medium text-white/80"
          >
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={`
            w-full px-4 py-3 rounded-xl
            bg-[rgba(20,20,30,0.95)] border
            text-white placeholder-white/40
            transition-all duration-200
            focus:outline-none focus:ring-2 focus:ring-[#00f5ff]/50
            ${
              error
                ? "border-red-500/50 focus:border-red-500"
                : "border-white/10 focus:border-white/20"
            }
            ${className}
          `}
          {...props}
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        {helperText && !error && (
          <p className="text-sm text-white/40">{helperText}</p>
        )}
      </div>
    );
  }
);

Input.displayName = "Input";
