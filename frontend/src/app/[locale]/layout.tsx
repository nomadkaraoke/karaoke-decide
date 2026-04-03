import type { Metadata } from "next";
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, setRequestLocale } from 'next-intl/server';
import { hasLocale } from 'next-intl';
import { notFound } from 'next/navigation';
import { routing } from '@/i18n/routing';
import { AuthProvider } from "@/contexts/AuthContext";
import { Navigation } from "@/components/Navigation";
import { ThemeProvider } from "@/components/theme-provider";
import { GoogleAnalytics } from "@/components/GoogleAnalytics";

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export const metadata: Metadata = {
  title: "Nomad Karaoke Decide - Find Your Next Karaoke Song",
  description: "Search 275,000+ karaoke songs. Discover popular tracks, find songs by artist, and decide what to sing next.",
  keywords: ["karaoke", "songs", "music", "singing", "karaoke songs", "song search"],
  authors: [{ name: "Nomad Karaoke" }],
  openGraph: {
    title: "Nomad Karaoke Decide",
    description: "Find your next karaoke song from 275,000+ tracks",
    url: "https://decide.nomadkaraoke.com",
    siteName: "Nomad Karaoke Decide",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Nomad Karaoke Decide",
    description: "Find your next karaoke song from 275,000+ tracks",
  },
  manifest: "/manifest.json",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;

  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }

  setRequestLocale(locale);

  const messages = await getMessages();

  return (
    <NextIntlClientProvider locale={locale} messages={messages}>
      <script
        dangerouslySetInnerHTML={{
          __html: `document.documentElement.lang="${locale}";`,
        }}
      />
      <GoogleAnalytics />
      <ThemeProvider
        attribute="class"
        defaultTheme="dark"
        enableSystem
        disableTransitionOnChange
      >
        <AuthProvider>
          <Navigation />
          {/* Spacer for fixed navigation - nav is ~72px (py-4 + content) */}
          <div className="h-[72px]" />
          {children}
        </AuthProvider>
      </ThemeProvider>
    </NextIntlClientProvider>
  );
}
