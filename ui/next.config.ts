import type { NextConfig } from "next";

const API_TARGET = process.env.AGI_API_URL ?? "http://localhost:8000";

// `output: "export"` makes `next build` emit a static site to `out/`, which
// FastAPI serves in production (`agi serve`). Rewrites are only honored by
// `next dev` — in production the UI talks to the FastAPI origin directly,
// so no proxy is needed.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  typescript: { ignoreBuildErrors: true },
  eslint: { ignoreDuringBuilds: true },
  // Prevent the dev-server file watcher from seeing agent worktrees.
  // Each worktree has a ui/ copy that confuses webpack and corrupts .next.
  webpack: (config, { dev }) => {
    if (dev) {
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          "**/node_modules/**",
          "**/.worktrees/**",
          "**/.venv/**",
          "**/.git/**",
        ],
      };
    }
    return config;
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_TARGET}/:path*`,
      },
    ];
  },
};

export default nextConfig;
