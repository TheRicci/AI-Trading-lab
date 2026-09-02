# Trading Floor — Premium Terminal UI

A high-fidelity, typography-focused financial terminal interface built to monitor the AI Bot Arena in real-time. Designed with a clean, warm-tinted dark aesthetic and structured data layout.

## Core Features

- **Trading Floor (`/`)**: Displays the bot leaderboard, dynamic portfolio comparison area chart, and the live reasoning feed pushed over Web PubSub.
- **Daily Analysis (`/analysis`)**: Deep dive into daily market **asymmetries** with executive summaries and collapsible detailed Markdown reports.
- **Short & Leverage Badges**: 
  - Displays red **`S`** badge on bots holding short (negative) positions.
  - Displays amber **`L`** badge on bots utilizing margin leverage (negative cash balances).
- **Session review mapping**: Natural-language mapping of trigger events (e.g. `session-open` → `AT OPEN`, `session-close` → `BEFORE CLOSE`).
- **Flexible Data Layer**: 
  - Detects `NEXT_PUBLIC_MOCK` and can run entirely offline using local JSON static simulation files.
  - Switches cleanly to real-time Azure Cosmos DB / Web PubSub integration when connected to the backend.

## Tech Stack

- **Core**: Next.js 15 (App Router), React 19, TypeScript
- **Styling**: Tailwind CSS (Custom terminal-inspired palette, custom animations)
- **Charts**: Recharts (Custom thin-stroke area charts with per-bot gradients)
- **Animations**: Framer Motion (Smooth entrance transitions for trade items)

## Run locally

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. With no `.env.local` API configuration, the app uses the bundled mock dataset automatically. To use Azure, copy `.env.local.example` to `.env.local`, set `NEXT_PUBLIC_API_BASE`, and leave `NEXT_PUBLIC_MOCK=false`.
