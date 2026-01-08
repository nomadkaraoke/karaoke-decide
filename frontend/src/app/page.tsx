"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { SparklesIcon, MicrophoneIcon, ClockIcon, MusicNoteIcon } from "@/components/icons";
import { LoadingOverlay } from "@/components/ui";

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
    <main className="min-h-screen animated-gradient">
      {/* Hero Section */}
      <section className="pt-16 pb-16 px-4">
        <div className="max-w-4xl mx-auto text-center">
          {/* App Icon */}
          <div className="w-24 h-24 mx-auto mb-6 rounded-3xl bg-gradient-to-br from-[var(--brand-pink)] via-[var(--brand-purple)] to-[var(--brand-blue)] p-0.5 shadow-lg shadow-[var(--brand-pink)]/20">
            <div className="w-full h-full rounded-3xl bg-[var(--bg)] flex items-center justify-center">
              <MicrophoneIcon className="w-12 h-12 text-[var(--text)]" />
            </div>
          </div>

          {/* Headline */}
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold text-[var(--text)] mb-6 leading-tight">
            Find Your Perfect
            <span className="block gradient-text">
              Karaoke Song
            </span>
          </h1>

          {/* Subheadline */}
          <p className="text-xl text-[var(--text-muted)] max-w-2xl mx-auto mb-8">
            Tell us what you like. We&apos;ll find songs you know and can actually sing.
          </p>

          {/* Primary CTA */}
          <a
            href="#"
            onClick={(e) => { e.preventDefault(); handleGetStarted(); }}
            className="inline-flex items-center gap-2 bg-[var(--primary)] hover:bg-[var(--primary-hover)] text-white font-semibold px-8 py-4 rounded-xl transition-all btn-glow"
          >
            {isStartingSession ? (
              "Starting..."
            ) : (
              <>
                Get Started
                <SparklesIcon className="w-5 h-5" />
              </>
            )}
          </a>

          {/* Trust signal */}
          <p className="text-[var(--text-subtle)] text-sm mt-4">
            No sign-up required • Takes 30 seconds
          </p>

          {/* Error message */}
          {error && (
            <div className="mt-4 px-4 py-3 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20 text-[var(--danger)] text-sm">
              {error}
            </div>
          )}
        </div>
      </section>

      {/* Feature Pills */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="grid md:grid-cols-3 gap-6">
            <FeaturePill
              icon={<MusicNoteIcon className="w-8 h-8 text-[var(--brand-pink)]" />}
              title="275K+ Songs"
              description="Massive karaoke catalog from KaraokeNerds"
            />
            <FeaturePill
              icon={<SparklesIcon className="w-8 h-8 text-[var(--brand-purple)]" />}
              title="Personalized"
              description="Matches songs to your music taste"
            />
            <FeaturePill
              icon={<ClockIcon className="w-8 h-8 text-[var(--brand-blue)]" />}
              title="Instant Results"
              description="Find your perfect song in seconds"
            />
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="py-16 px-4 bg-[var(--card)]/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-12">How It Works</h2>
          <div className="grid md:grid-cols-3 gap-8">
            <Step number={1} title="Quick Quiz" description="Tell us your favorite genres and decades" />
            <Step number={2} title="Get Matches" description="See songs personalized to your taste" />
            <Step number={3} title="Sing!" description="Find karaoke links for any song" />
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 border-t border-[var(--primary)]/30">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-8" />
          </div>
          <div className="text-sm text-[var(--text-subtle)]">
            Powered by KaraokeNerds + Spotify data
          </div>
          <div className="flex gap-6 text-sm text-[var(--text-muted)]">
            <a href="https://karaokenerds.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--primary)] transition-colors">
              KaraokeNerds
            </a>
            <a href="https://nomadkaraoke.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--primary)] transition-colors">
              About
            </a>
          </div>
        </div>
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
    <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-xl p-6 card-hover">
      <div className="w-16 h-16 bg-[var(--primary)]/20 rounded-2xl flex items-center justify-center mx-auto mb-4">
        {icon}
      </div>
      <h3 className="font-semibold text-lg mb-2 text-center">{title}</h3>
      <p className="text-[var(--text-muted)] text-sm text-center">{description}</p>
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
      <div className="w-16 h-16 bg-[var(--primary)]/20 rounded-2xl flex items-center justify-center mx-auto mb-4">
        <span className="text-2xl font-bold text-[var(--primary)]">{number}</span>
      </div>
      <h3 className="font-semibold text-lg mb-2">{title}</h3>
      <p className="text-[var(--text-muted)] text-sm">{description}</p>
    </div>
  );
}
