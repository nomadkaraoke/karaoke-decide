"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Redirect from old /services route to new /my-data page
 */
export default function ServicesRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/my-data");
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <p className="text-[var(--text-muted)]">Redirecting...</p>
    </div>
  );
}
