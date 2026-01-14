import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: {
    unoptimized: true,
  },
  // For GitHub Pages deployment at decide.nomadkaraoke.com
  // No basePath needed with custom domain

  // Proxy /api/* to prod backend during local development
  // This avoids CORS issues when testing locally against prod
  // Note: rewrites only work during `next dev`, not in static export
  async rewrites() {
    // Only enable proxy in dev mode when PROXY_TO_PROD is set
    if (process.env.PROXY_TO_PROD) {
      return [
        {
          source: "/api/:path*",
          destination: "https://decide.nomadkaraoke.com/api/:path*",
        },
      ];
    }
    return [];
  },
};

export default nextConfig;
