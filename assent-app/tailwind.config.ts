import type { Config } from "tailwindcss";

// Palette mirrors site/index.html (the Etch marketing site) so the two feel
// like the same product when stitched together by Caddy.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0a0a0f",
        surface: "#12121a",
        elevated: "#1a1a26",
        border: "#2a2a3a",
        text: "#e4e4ef",
        "text-dim": "#8888a0",
        "text-muted": "#55556a",
        accent: "#7c6aff",
        "accent-dim": "#5a4ad4",
        success: "#34d399",
        danger: "#f87171",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Inter", "Helvetica", "Arial", "sans-serif"],
        mono: ["SF Mono", "Cascadia Code", "Fira Code", "Consolas", "monospace"],
      },
      boxShadow: {
        glow: "0 0 60px rgba(124, 106, 255, 0.15)",
      },
    },
  },
  plugins: [],
};

export default config;
