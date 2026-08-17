import type { Metadata } from "next";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trading Floor — AI Bot Arena",
  description: "Six AI bots, three strategies, two LLM providers. Real money rules, none of it real.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body>
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <header className="border-b border-border bg-surface">
          <div className="mx-auto max-w-[1300px] px-6">

            {/* Top bar: logo + status */}
            <div className="flex items-center justify-between py-4">
              <div className="flex items-center gap-3">
                {/* Diamond logo mark */}
                <span className="text-amber font-mono text-base leading-none">◆</span>
                <div>
                  <div className="font-mono text-[13px] font-semibold tracking-[0.12em] text-ink uppercase">
                    Trading Floor
                  </div>
                  <div className="font-mono text-[9px] tracking-[0.14em] text-dim uppercase mt-0.5">
                    AI Bot Arena · Simulated
                  </div>
                </div>
              </div>

              {/* Right: thin meta info */}
              <div className="hidden sm:flex items-center gap-4 font-mono text-[10px] text-dim tracking-widest">
                <span>3 STRATEGIES</span>
                <span className="text-border">|</span>
                <span>2 PROVIDERS</span>
                <span className="text-border">|</span>
                <span>6 BOTS</span>
              </div>
            </div>

            {/* Nav tabs flush to bottom of header */}
            <Nav />
          </div>
        </header>

        {/* ── Content ────────────────────────────────────────────────────── */}
        <main className="mx-auto max-w-[1300px] px-6 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
