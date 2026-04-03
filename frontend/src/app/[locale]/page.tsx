"use client";

import { useState, useEffect } from "react";
import { useRouter } from "@/i18n/routing";
import Image from "next/image";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("home");
  const tCommon = useTranslations("common");
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
      setError(t("failedToStart"));
    } finally {
      setIsStartingSession(false);
    }
  };

  // Show loading while checking auth/quiz status or redirecting
  if (authLoading || quizStatusLoading || isAuthenticated) {
    return <LoadingOverlay message={tCommon("loading")} />;
  }

  return (
    <main className="min-h-screen animated-gradient">
      {/* Hero Section - Clean, Generator-style */}
      <section className="pt-16 pb-16 px-4">
        <div className="max-w-4xl mx-auto text-center">
          {/* Large headline matching Generator style */}
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold text-[var(--text)] mb-6 leading-tight">
            {t("heroTitle")}{" "}
            <span className="gradient-text">{t("heroTitleHighlight")}</span>
            {" "}{t("heroTitleEnd")}
          </h1>

          {/* Subheadline */}
          <p className="text-lg md:text-xl max-w-2xl mx-auto mb-10">
            {t("heroSubtitle")}
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
            {t("findYourSong")}
            <SparklesIcon className="w-5 h-5 ml-2" />
          </Button>

          {/* Trust signal */}
          <p className="text-[var(--text-subtle)] text-sm mt-4">
            {t("trustSignal")}
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
            {t("soundFamiliar")}
          </h2>
          <p className="text-[var(--text-muted)] text-center max-w-2xl mx-auto mb-12">
            {t("soundFamiliarSubtitle")}
          </p>

          <div className="grid md:grid-cols-2 gap-6">
            <ProblemCard
              emoji="🤷"
              title={t("problemNothingTitle")}
              description={t("problemNothingDesc")}
            />
            <ProblemCard
              emoji="😬"
              title={t("problemSingTitle")}
              description={t("problemSingDesc")}
            />
            <ProblemCard
              emoji="😶"
              title={t("problemCareTitle")}
              description={t("problemCareDesc")}
            />
            <ProblemCard
              emoji="❓"
              title={t("problemKnowTitle")}
              description={t("problemKnowDesc")}
            />
          </div>
        </div>
      </section>

      {/* Two Paths Section */}
      <section className="py-16 px-4 bg-[var(--card)]/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-4">
            {t("twoWaysTitle")}
          </h2>
          <p className="text-[var(--text-muted)] text-center max-w-2xl mx-auto mb-12">
            {t("twoWaysSubtitle")}
          </p>

          <div className="grid md:grid-cols-2 gap-8">
            {/* Quiz Path */}
            <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-2xl p-8 card-hover">
              <div className="w-14 h-14 bg-[var(--brand-purple)]/20 rounded-xl flex items-center justify-center mb-6">
                <SparklesIcon className="w-7 h-7 text-[var(--brand-purple)]" />
              </div>
              <h3 className="text-xl font-bold mb-2">{t("quickQuizTitle")}</h3>
              <p className="text-[var(--text-muted)] mb-6">
                {t("quickQuizDesc")}
              </p>
              <div className="space-y-3 text-sm">
                <PathStep number={1} text={t("quickQuizStep1")} />
                <PathStep number={2} text={t("quickQuizStep2")} />
                <PathStep number={3} text={t("quickQuizStep3")} />
              </div>
              <div className="mt-6 pt-6 border-t border-[var(--card-border)]">
                <p className="text-xs text-[var(--text-subtle)]">{t("quickQuizTime")}</p>
              </div>
            </div>

            {/* Data Sources Path */}
            <div className="bg-[var(--card)] border border-[var(--card-border)] rounded-2xl p-8 card-hover">
              <div className="w-14 h-14 bg-[var(--brand-pink)]/20 rounded-xl flex items-center justify-center mb-6">
                <MusicIcon className="w-7 h-7 text-[var(--brand-pink)]" />
              </div>
              <h3 className="text-xl font-bold mb-2">{t("connectMusicTitle")}</h3>
              <p className="text-[var(--text-muted)] mb-6">
                {t("connectMusicDesc")}
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
                <p className="text-xs text-[var(--text-subtle)]">{t("connectMusicPowerUsers")}</p>
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
                  {t("poweredByNomad")}
                </div>
                <h2 className="text-2xl sm:text-3xl font-bold mb-4">
                  {t("anySongCanBeKaraoke")}
                </h2>
                <p className="text-[var(--text-muted)] mb-6">
                  {t("anySongDesc")}
                </p>
                <a
                  href="https://gen.nomadkaraoke.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 text-[var(--brand-purple)] hover:text-[var(--brand-pink)] transition-colors font-medium"
                >
                  {t("tryTheGenerator")}
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
            {t("seeItInAction")}
          </h2>
          <p className="text-[var(--text-muted)] text-center max-w-2xl mx-auto mb-12">
            {t("seeItInActionSubtitle")}
          </p>

          <div className="grid md:grid-cols-3 gap-8">
            <ScreenshotCard
              src="/screenshots/quiz.avif"
              alt={t("screenshotQuizAlt")}
              title={t("screenshotQuizTitle")}
              description={t("screenshotQuizDesc")}
            />
            <ScreenshotCard
              src="/screenshots/recommendations.avif"
              alt={t("screenshotRecsAlt")}
              title={t("screenshotRecsTitle")}
              description={t("screenshotRecsDesc")}
            />
            <ScreenshotCard
              src="/screenshots/my-data.avif"
              alt={t("screenshotDataAlt")}
              title={t("screenshotDataTitle")}
              description={t("screenshotDataDesc")}
            />
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-16 px-4">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-2xl sm:text-3xl font-bold text-center mb-12">
            {t("whatMakesItWork")}
          </h2>
          <div className="grid md:grid-cols-3 gap-6">
            <FeatureCard
              icon={<MusicIcon className="w-7 h-7 text-[var(--brand-pink)]" />}
              title={t("feature275kTitle")}
              description={t("feature275kDesc")}
            />
            <FeatureCard
              icon={<SparklesIcon className="w-7 h-7 text-[var(--brand-purple)]" />}
              title={t("featurePersonalizedTitle")}
              description={t("featurePersonalizedDesc")}
            />
            <FeatureCard
              icon={<SearchIcon className="w-7 h-7 text-[var(--brand-blue)]" />}
              title={t("featureFilteringTitle")}
              description={t("featureFilteringDesc")}
            />
          </div>
        </div>
      </section>

      {/* Final CTA Section */}
      <section className="py-20 px-4">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-2xl sm:text-3xl font-bold mb-4">
            {t("readyToFind")}
          </h2>
          <p className="text-[var(--text-muted)] mb-8">
            {t("readyToFindSubtitle")}
          </p>
          <Button
            variant="primary"
            size="lg"
            onClick={handleGetStarted}
            isLoading={isStartingSession}
            disabled={isStartingSession}
            className="px-8 py-4 text-lg btn-glow"
          >
            {t("getStartedFree")}
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
              {t("footerKaraokeNerds")}
            </a>
            <a href="https://gen.nomadkaraoke.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--primary)] transition-colors">
              {t("footerGenerator")}
            </a>
            <a href="https://nomadkaraoke.com" target="_blank" rel="noopener noreferrer" className="hover:text-[var(--primary)] transition-colors">
              {t("footerAbout")}
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
          className="object-cover object-top group-hover:scale-105 transition-transform duration-300"
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
