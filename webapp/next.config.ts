import type { NextConfig } from "next";

// The FastAPI server owns all data. The browser talks only to Next.js, which forwards
// these paths, so the page needs no CORS and works offline.
const API = process.env.COMMUTE_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/basemap.geojson", destination: `${API}/basemap.geojson` },
    ];
  },
};

export default nextConfig;
