import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: {
    unoptimized: true,
  },
  // For GitHub Pages deployment at decide.nomadkaraoke.com
  // No basePath needed with custom domain
};

export default nextConfig;
