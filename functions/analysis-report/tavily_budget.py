"""Minimal Tavily monthly-budget tracker.

Permissive by design: this project is happy running close to the 1,000-free
credits/month Tavily limit. The tracker logs loudly as usage approaches the cap
and hard-refuses only once it is exceeded, so a runaway loop can never blow
way past 1,000 while normal operation is never interrupted.

Storage: a small JSON file in the per-app temp dir, keyed by YYYY-MM. Each
Function App (and the local backtest) keeps its own count, which is a slight
under-count of a single global pool, but adequate as a guardrail.
"""
import json, os, tempfile, datetime, logging

# The Tavily free tier grants 1,000 credits/month (basic search = 1 credit).
MONTHLY_LIMIT = int(os.environ.get("TAVILY_MONTHLY_LIMIT", "1000"))
# Above this, start warning loudly but keep working ("close to 1k is fine").
WARN_AT = int(os.environ.get("TAVILY_WARN_AT", "850"))


class TavilyBudget:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(
            tempfile.gettempdir(), "tavily_budget.json")
        self.month = datetime.date.today().strftime("%Y-%m")
        self.used = self._load()

    def _key(self):
        return f"{self.month}"

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("month") != self.month:
                return 0
            return int(data.get("used", 0))
        except Exception:
            return 0

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"month": self.month, "used": self.used}, fh)
        except Exception:
            pass

    def allowance(self, n: int = 1) -> bool:
        """Whether we may run n more searches."""
        return (self.used + n) <= MONTHLY_LIMIT

    def spend(self, n: int = 1):
        """Record n searches and log a warning near/at the cap."""
        self.used += n
        self._save()
        if self.used >= MONTHLY_LIMIT:
            logging.warning(
                f"[TavilyBudget] Monthly limit ({MONTHLY_LIMIT}) reached ({self.used}). "
                f"Further searches will be blocked until next month.")
        elif self.used >= WARN_AT:
            logging.warning(
                f"[TavilyBudget] Approaching monthly limit: {self.used}/{MONTHLY_LIMIT} used.")
        return self.used

    def status(self) -> str:
        return f"{self.used}/{MONTHLY_LIMIT} Tavily credits used this month ({self.month})"
