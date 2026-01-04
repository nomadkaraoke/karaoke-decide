"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { SparklesIcon, MicrophoneIcon, ClockIcon, MusicNoteIcon } from "@/components/icons";
import { LoadingOverlay, Button } from "@/components/ui";

export default function Home() {
  const router = useRouter();
  const {
    isAuthenticated,
    isLoading: authLoading,
    hasCompletedQuiz,
    quizStatusLoading,
    startGuestSession,
  } = useAuth();
  const [isStartingSession, setIsStartingSession] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Redirect authenticated users based on quiz completion status
  useEffect(() => {
    if (!authLoading && !quizStatusLoading && isAuthenticated) {
      if (hasCompletedQuiz) {
        router.push("/recommendations");
      } else {
        router.push("/quiz");
      }
    }
  }, [authLoading, quizStatusLoading, isAuthenticated, hasCompletedQuiz, router]);

  const handleGetStarted = async () => {
    if (isAuthenticated) {
      // Already authenticated - go to quiz (quiz page will handle if already completed)
      router.push("/quiz");
      return;
    }

    setIsStartingSession(true);
    setError(null);
    try {
      await startGuestSession();
      // After creating session, go to quiz
      router.push("/quiz");
    } catch (err) {
      console.error("Failed to start session:", err);
      setError("Failed to start. Please try again.");
    } finally {
      setIsStartingSession(false);
    }
  };

  // Show loading while checking auth/quiz status or redirecting
  if (authLoading || quizStatusLoading || isAuthenticated) {
    return <LoadingOverlay message="Loading..." />;
  }

  return (
    <main className="relative min-h-screen pb-safe flex flex-col">
      <div className="flex-1 flex flex-col justify-center max-w-2xl mx-auto px-4 py-8">
        {/* Hero Section */}
        <div className="text-center mb-12">
          {/* App Icon */}
          <div className="w-24 h-24 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-[#ff2d92] via-[#b347ff] to-[#00f5ff] p-0.5 shadow-lg shadow-[#ff2d92]/20">
            <div className="w-full h-full rounded-3xl bg-[#0a0a0f] flex items-center justify-center">
              <MicrophoneIcon className="w-12 h-12 text-white" />
            </div>
          </div>

          {/* Headline */}
          <h1 className="text-4xl md:text-5xl font-bold text-white mb-4 leading-tight">
            Find Your Perfect
            <span className="block bg-gradient-to-r from-[#ff2d92] via-[#b347ff] to-[#00f5ff] bg-clip-text text-transparent">
              Karaoke Song
            </span>
          </h1>

          {/* Subheadline */}
          <p className="text-lg text-white/60 max-w-md mx-auto mb-8">
            Tell us what you like. We&apos;ll find songs you know and can actually sing.
          </p>

          {/* Primary CTA */}
          <div className="mb-4">
            <Button
              variant="primary"
              size="lg"
              onClick={handleGetStarted}
              isLoading={isStartingSession}
              leftIcon={<SparklesIcon className="w-5 h-5" />}
              className="px-8 py-4 text-lg"
            >
              Get Started
            </Button>
          </div>

          {/* Trust signal */}
          <p className="text-white/40 text-sm">
            No sign-up required • Takes 30 seconds
          </p>

          {/* Error message */}
          {error && (
            <div className="mt-4 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {error}
            </div>
          )}
        </div>

        {/* Feature Pills */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-12">
          <FeaturePill
            icon={<MusicNoteIcon className="w-5 h-5 text-[#ff2d92]" />}
            title="275K+ Songs"
            description="Massive karaoke catalog"
          />
          <FeaturePill
            icon={<SparklesIcon className="w-5 h-5 text-[#b347ff]" />}
            title="Personalized"
            description="Matches your taste"
          />
          <FeaturePill
            icon={<ClockIcon className="w-5 h-5 text-[#00f5ff]" />}
            title="Instant Results"
            description="Find songs in seconds"
          />
        </div>

        {/* How it works */}
        <div className="rounded-2xl bg-white/5 border border-white/10 p-6">
          <h2 className="text-lg font-semibold text-white mb-4 text-center">
            How it works
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            <Step number={1} title="Quick Quiz" description="Tell us your favorite genres and decades" />
            <Step number={2} title="Get Matches" description="See songs personalized to your taste" />
            <Step number={3} title="Sing!" description="Find karaoke links for any song" />
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="text-center py-6 border-t border-white/5">
        <p className="text-white/30 text-sm">
          Powered by KaraokeNerds + Spotify data
        </p>
        <p className="text-white/20 text-xs mt-1">
          From the creators of{" "}
          <a
            href="https://karaokenerds.com"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#ff2d92]/60 hover:text-[#ff2d92] transition-colors"
          >
            KaraokeNerds
          </a>
        </p>
      </footer>
    </main>
  );
}

function FeaturePill({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="flex items-center gap-3 p-4 rounded-xl bg-white/5 border border-white/10">
      <div className="flex-shrink-0 w-10 h-10 rounded-full bg-white/5 flex items-center justify-center">
        {icon}
      </div>
      <div>
        <p className="font-medium text-white text-sm">{title}</p>
        <p className="text-white/50 text-xs">{description}</p>
      </div>
    </div>
  );
}

function Step({
  number,
  title,
  description,
}: {
  number: number;
  title: string;
  description: string;
}) {
  return (
    <div className="text-center">
      <div className="w-10 h-10 mx-auto mb-3 rounded-full bg-gradient-to-br from-[#ff2d92]/20 to-[#b347ff]/20 border border-[#ff2d92]/30 flex items-center justify-center">
        <span className="text-[#ff2d92] font-bold">{number}</span>
      </div>
      <h3 className="font-semibold text-white text-sm mb-1">{title}</h3>
      <p className="text-white/50 text-xs">{description}</p>
    </div>
  );
}
