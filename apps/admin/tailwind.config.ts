import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#0f766e", // teal-700 — placeholder Heissal accent
          fg: "#ffffff",
        },
      },
    },
  },
  plugins: [],
};

export default config;
