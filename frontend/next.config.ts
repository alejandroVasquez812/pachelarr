import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  rewrites: () => [
    {
      source: "/api/:path*",
      destination: `${process.env.PACHELARR_BACKEND_URL || "http://localhost:6800"}/:path*`,
    },
  ],
};

export default nextConfig;
