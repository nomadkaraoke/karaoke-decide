"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Redirect from old /my-songs route to new /my-data page
 */
export default function MySongsRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/my-data");
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <p className="text-white/60">Redirecting...</p>
    </div>
  );
}
