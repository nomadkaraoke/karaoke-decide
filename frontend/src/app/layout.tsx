import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AuthProvider } from "@/contexts/AuthContext";
import { Navigation } from "@/components/Navigation";

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

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  themeColor: "#0a0a0f",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <AuthProvider>
          <Navigation />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
