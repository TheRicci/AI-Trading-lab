import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Surfaces — warm-tinted near-black
        bg:          "#0a0a0d",
        surface:     "#111116",
        "surface-hi":"#18181f",
        // Borders
        border:      "#252530",
        "border-hi": "#38384a",
        // Primary accent — amber (the ONLY chromatic color besides status)
        amber:       "#f0a429",
        "amber-dim": "rgba(240,164,41,0.10)",
        // Text scale
        ink:         "#f0f0f2",
        secondary:   "#9898a8",
        dim:         "#4e4e5e",
        // Status
        up:          "#22c55e",
        "up-dim":    "rgba(34,197,94,0.10)",
        down:        "#ef4444",
        "down-dim":  "rgba(239,68,68,0.10)",
        // Provider tints
        gemini:      "#7ba7f5",
        "gemini-dim":"rgba(123,167,245,0.10)",
        groq:        "#f5945a",
        "groq-dim":  "rgba(245,148,90,0.10)",
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "monospace"],
        sans: ["'Inter'", "system-ui", "sans-serif"],
      },
      fontSize: {
        "2xs": ["10px", { lineHeight: "14px", letterSpacing: "0.08em" }],
      },
      keyframes: {
        "bar-in": {
          "0%":   { transform: "scaleX(0)" },
          "100%": { transform: "scaleX(1)" },
        },
        "fade-down": {
          "0%":   { opacity: "0", transform: "translateY(-6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "feed-flash": {
          "0%":   { backgroundColor: "rgba(240,164,41,0.07)" },
          "100%": { backgroundColor: "transparent" },
        },
        blink: {
          "0%,100%": { opacity: "1" },
          "50%":     { opacity: "0.15" },
        },
      },
      animation: {
        "bar-in":     "bar-in 0.7s cubic-bezier(0.16,1,0.3,1) both",
        "fade-down":  "fade-down 0.25s ease-out both",
        "feed-flash": "feed-flash 2s ease-out forwards",
        blink:        "blink 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
