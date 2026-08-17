"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/",         label: "Trading Floor" },
  { href: "/analysis", label: "Daily Analysis" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-0 -mb-px">
      {TABS.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={[
              "relative px-4 py-2.5 font-mono text-[11px] tracking-[0.08em] uppercase transition-colors duration-150",
              active
                ? "text-amber amber-line"
                : "text-dim hover:text-secondary",
            ].join(" ")}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
