"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { useAuth } from "@/contexts/AuthContext";
import {
  SparklesIcon,
  MicrophoneIcon,
  MusicIcon,
  SearchIcon,
  ChevronRightIcon,
  SpotifyIcon,
  LastfmIcon,
  ExternalLinkIcon,
} from "@/components/icons";
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
      router.push("/quiz");
      return;
    }

    setIsStartingSession(true);
    setError(null);
    try {
      await startGuestSession();
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
      {/* Hero Section - Clean, Generator-style */}
      <section className="pt-20 pb-16 px-4">
        <div className="max-w-4xl mx-auto text-center">
          {/* Large headline matching Generator style */}
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold text-[var(--text)] mb-6 leading-tight">
            Easily Choose{" "}
            <span className="gradient-text">Karaoke Songs</span>
            {" "}to Sing
          </h1>

          {/* Subheadline */}
          <p className="text-lg md:text-xl text-[var(--text-muted)] max-w-2xl mx-auto mb-10">
            Find songs you know, can actually sing, and that the crowd will love. Personalized recommendations in 30 seconds.
          </p>

          {/* Primary CTA */}
          <Button
            variant="primary"
            size="lg"
            onClick={handleGetStarted}
            isLoading={isStartingSession}
            disabled={isStartingSession}
            className="px-8 py-4 text-lg btn-glow"
          >
            Find Your Song
            <SparklesIcon className="w-5 h-5 ml-2" />
          </Button>

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

      {/* The Problem Section */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-4">
            Sound familiar?
          </h2>
          <p className="text-[var(--text-muted)] text-center max-w-2xl mx-auto mb-12">
            You&apos;re at karaoke, flipping through the songbook, and...
          </p>

          <div className="grid md:grid-cols-2 gap-6">
            <ProblemCard
              emoji="🤷"
              title="Nothing jumps out"
              description="The book has thousands of songs but you're drawing a blank on what to pick"
            />
            <ProblemCard
              emoji="😬"
              title="Can I even sing this?"
              description="You love the song but have no idea if it's in your vocal range"
            />
            <ProblemCard
              emoji="😶"
              title="Will anyone care?"
              description="You want something that gets the room going, not awkward silence"
            />
            <ProblemCard
              emoji="❓"
              title="I don't know these songs"
              description="Half the catalog is stuff you've never heard of"
            />
          </div>
        </div>
      </section>

      {/* Two Paths Section */}
      <section className="py-16 px-4 bg-[var(--card)]/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-4">
            Two ways to get started
          </h2>
          <p className="text-[var(--text-muted)] text-center max-w-2xl mx-auto mb-12">
            Whether you have streaming data or not, we&apos;ve got you covered
          </p>

          <div className="grid md:grid-cols-2 gap-8">
            {/* Quiz Path */}
            <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-2xl p-8 card-hover">
              <div className="w-14 h-14 bg-[var(--brand-purple)]/20 rounded-xl flex items-center justify-center mb-6">
                <SparklesIcon className="w-7 h-7 text-[var(--brand-purple)]" />
              </div>
              <h3 className="text-xl font-bold mb-2">Quick Quiz</h3>
              <p className="text-[var(--text-muted)] mb-6">
                Perfect if you just want quick recommendations. Answer 3 questions about your music taste and go.
              </p>
              <div className="space-y-3 text-sm">
                <PathStep number={1} text="Pick your favorite genres" />
                <PathStep number={2} text="Choose your decades" />
                <PathStep number={3} text="Select artists you like" />
              </div>
              <div className="mt-6 pt-6 border-t border-[var(--card-border)]">
                <p className="text-xs text-[var(--text-subtle)]">Takes about 30 seconds</p>
              </div>
            </div>

            {/* Data Sources Path */}
            <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-2xl p-8 card-hover">
              <div className="w-14 h-14 bg-[var(--brand-pink)]/20 rounded-xl flex items-center justify-center mb-6">
                <MusicIcon className="w-7 h-7 text-[var(--brand-pink)]" />
              </div>
              <h3 className="text-xl font-bold mb-2">Connect Your Music</h3>
              <p className="text-[var(--text-muted)] mb-6">
                Already have Spotify or Last.fm? Connect them for personalized recommendations based on what you actually listen to.
              </p>
              <div className="flex items-center gap-4 mb-6">
                <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--secondary)]">
                  <SpotifyIcon className="w-5 h-5 text-[#1DB954]" />
                  <span className="text-sm">Spotify</span>
                </div>
                <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--secondary)]">
                  <LastfmIcon className="w-5 h-5 text-[#d51007]" />
                  <span className="text-sm">Last.fm</span>
                </div>
              </div>
              <div className="mt-6 pt-6 border-t border-[var(--card-border)]">
                <p className="text-xs text-[var(--text-subtle)]">Best recommendations for power users</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Generator Integration Section */}
      <section className="py-16 px-4">
        <div className="max-w-4xl mx-auto">
          <div className="bg-gradient-to-br from-[var(--brand-purple)]/20 via-[var(--brand-pink)]/10 to-[var(--brand-blue)]/20 border border-[var(--brand-purple)]/30 rounded-2xl p-8 md:p-12">
            <div className="flex flex-col md:flex-row items-center gap-8">
              <div className="flex-1">
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[var(--brand-purple)]/20 text-[var(--brand-purple)] text-sm font-medium mb-4">
                  <SparklesIcon className="w-4 h-4" />
                  Powered by Nomad Karaoke
                </div>
                <h2 className="text-2xl sm:text-3xl font-bold mb-4">
                  Any song can be karaoke
                </h2>
                <p className="text-[var(--text-muted)] mb-6">
                  Found the perfect song but no karaoke version exists? No problem. Our Generator can create a karaoke track for almost any song in under 30 minutes—so you can sing it the same night.
                </p>
                <a
                  href="https://gen.nomadkaraoke.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-[var(--brand-purple)] hover:text-[var(--brand-pink)] transition-colors font-medium"
                >
                  Try the Generator
                  <ExternalLinkIcon className="w-4 h-4" />
                </a>
              </div>
              <div className="flex-shrink-0">
                <div className="w-32 h-32 rounded-2xl bg-gradient-to-br from-[var(--brand-pink)] via-[var(--brand-purple)] to-[var(--brand-blue)] p-1">
                  <div className="w-full h-full rounded-2xl bg-[var(--bg)] flex items-center justify-center">
                    <MicrophoneIcon className="w-16 h-16 text-[var(--brand-purple)]" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Screenshots Section */}
      <section className="py-16 px-4 bg-[var(--card)]/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-4">
            See it in action
          </h2>
          <p className="text-[var(--text-muted)] text-center max-w-2xl mx-auto mb-12">
            From quick quiz to personalized recommendations in seconds
          </p>

          <div className="grid md:grid-cols-3 gap-8">
            <ScreenshotCard
              src="/screenshots/quiz-genres.png"
              alt="Quiz - Select your favorite genres"
              title="1. Quick Quiz"
              description="Tell us what genres and decades you love"
            />
            <ScreenshotCard
              src="/screenshots/recommendations.png"
              alt="Personalized song recommendations"
              title="2. Get Matches"
              description="See songs matched to your taste"
            />
            <ScreenshotCard
              src="/screenshots/my-data.png"
              alt="Your music data overview"
              title="3. Your Data"
              description="Full transparency on what we know about you"
            />
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-12">
            What makes it work
          </h2>
          <div className="grid md:grid-cols-3 gap-6">
            <FeatureCard
              icon={<MusicIcon className="w-7 h-7 text-[var(--brand-pink)]" />}
              title="275K+ Existing Tracks"
              description="Database of all existing karaoke songs"
            />
            <FeatureCard
              icon={<SparklesIcon className="w-7 h-7 text-[var(--brand-purple)]" />}
              title="Personalized"
              description="Matches songs to your actual music taste"
            />
            <FeatureCard
              icon={<SearchIcon className="w-7 h-7 text-[var(--brand-blue)]" />}
              title="Smart Filtering"
              description="Filter by decade, genre, popularity & more"
            />
          </div>
        </div>
      </section>

      {/* Final CTA Section */}
      <section className="py-20 px-4">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-2xl sm:text-3xl font-bold mb-4">
            Ready to find your next song?
          </h2>
          <p className="text-[var(--text-muted)] mb-8">
            Stop scrolling through endless song lists. Get personalized karaoke recommendations in under a minute.
          </p>
          <Button
            variant="primary"
            size="lg"
            onClick={handleGetStarted}
            isLoading={isStartingSession}
            disabled={isStartingSession}
            className="px-8 py-4 text-lg btn-glow"
          >
            Get Started Free
            <ChevronRightIcon className="w-5 h-5 ml-2" />
          </Button>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 px-4 border-t border-[var(--primary)]/30">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Image src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" width={140} height={50} className="h-8 w-auto" />
          </div>
          <div className="flex gap-6 text-sm text-[var(--text-muted)]">
            <a href="https://karaokenerds.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--primary)] transition-colors">
              KaraokeNerds
            </a>
            <a href="https://gen.nomadkaraoke.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--primary)] transition-colors">
              Generator
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

function ProblemCard({
  emoji,
  title,
  description,
}: {
  emoji: string;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-xl p-6 card-hover">
      <div className="flex items-start gap-4">
        <div className="text-3xl flex-shrink-0">{emoji}</div>
        <div>
          <h3 className="font-semibold text-lg mb-1">{title}</h3>
          <p className="text-[var(--text-muted)] text-sm">{description}</p>
        </div>
      </div>
    </div>
  );
}

function PathStep({ number, text }: { number: number; text: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-6 h-6 rounded-full bg-[var(--brand-purple)]/20 flex items-center justify-center flex-shrink-0">
        <span className="text-xs font-bold text-[var(--brand-purple)]">{number}</span>
      </div>
      <span className="text-[var(--text-muted)]">{text}</span>
    </div>
  );
}

function ScreenshotCard({
  src,
  alt,
  title,
  description,
}: {
  src: string;
  alt: string;
  title: string;
  description: string;
}) {
  return (
    <div className="group">
      <div className="relative aspect-[4/3] rounded-xl overflow-hidden mb-4 border border-[var(--card-border)] bg-[var(--card)]">
        <Image
          src={src}
          alt={alt}
          fill
          className="object-cover object-[center_15%] scale-125 group-hover:scale-130 transition-transform duration-300"
        />
      </div>
      <h3 className="font-semibold mb-1">{title}</h3>
      <p className="text-sm text-[var(--text-muted)]">{description}</p>
    </div>
  );
}

function FeatureCard({
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
      <div className="w-14 h-14 bg-[var(--primary)]/20 rounded-xl flex items-center justify-center mb-4">
        {icon}
      </div>
      <h3 className="font-semibold text-lg mb-2">{title}</h3>
      <p className="text-[var(--text-muted)] text-sm">{description}</p>
    </div>
  );
}
