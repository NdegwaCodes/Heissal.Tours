/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The FastAPI base URL the server-side BFF routes proxy to.
  env: {
    API_BASE_URL: process.env.API_BASE_URL ?? "http://localhost:8000",
  },
};

export default nextConfig;
