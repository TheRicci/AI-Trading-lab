import { PROVIDER_LABELS } from "@/lib/types";
import type { LlmProvider } from "@/lib/types";

const STYLE: Record<string, { text: string; dot: string }> = {
  gemini:      { text: "text-gemini",      dot: "bg-gemini"      },
  groq:        { text: "text-groq",        dot: "bg-groq"        },
  groq_tavily: { text: "text-groq-tavily", dot: "bg-groq-tavily" },
};

export function ProviderBadge({ provider }: { provider: LlmProvider }) {
  const s = STYLE[provider] ?? { text: "text-dim", dot: "bg-dim" };
  const label = PROVIDER_LABELS[provider] ?? provider;
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono text-[10px] tracking-[0.06em] uppercase ${s.text}`}>
      <span className={`w-1 h-1 rounded-full ${s.dot} flex-shrink-0`} />
      {label}
    </span>
  );
}
