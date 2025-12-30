"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CheckIcon, SpotifyIcon } from "@/components/icons";

export default function SpotifySuccessPage() {
  const router = useRouter();

  useEffect(() => {
    // Redirect to services page after a brief delay
    const timer = setTimeout(() => {
      router.push("/services");
    }, 2000);

    return () => clearTimeout(timer);
  }, [router]);

  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="text-center">
        <div className="relative w-20 h-20 mx-auto mb-6">
          <div className="absolute inset-0 bg-[#1DB954]/20 rounded-full animate-pulse" />
          <div className="relative w-full h-full rounded-full bg-[#1DB954]/10 flex items-center justify-center border border-[#1DB954]/30">
            <SpotifyIcon className="w-8 h-8 text-[#1DB954]" />
          </div>
          <div className="absolute -bottom-1 -right-1 w-8 h-8 rounded-full bg-green-500 flex items-center justify-center">
            <CheckIcon className="w-5 h-5 text-white" />
          </div>
        </div>

        <h1 className="text-2xl font-bold text-white mb-2">
          Spotify Connected!
        </h1>
        <p className="text-white/60">Redirecting you back...</p>
      </div>
    </main>
  );
}
