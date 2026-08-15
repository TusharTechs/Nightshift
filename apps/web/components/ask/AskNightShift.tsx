"use client";

import { useState } from "react";

import { askNightShift, ApiError } from "../../lib/api";

/**
 * Ask NightShift (Sprint 4 Step 4, Phase B). Vision doc, verbatim: "merchant
 * asks 'why did revenue increase yesterday,' gets the attributed answer."
 * Stateless client-side too — each submission is its own request/response
 * pair (see `lib/api.ts::askNightShift`'s own docstring on why there's no
 * persisted conversation history yet).
 */
export function AskNightShift({ shopDomain }: { shopDomain: string }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [usedLlm, setUsedLlm] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || isLoading) return;

    setIsLoading(true);
    setError(null);
    try {
      const result = await askNightShift(shopDomain, trimmed);
      setAnswer(result.answer);
      setUsedLlm(result.used_llm);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach NightShift AI. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section aria-labelledby="ask-nightshift-heading" className="space-y-3">
      <h2 id="ask-nightshift-heading" className="text-lg font-semibold text-gray-900">
        Ask NightShift
      </h2>

      <form onSubmit={handleSubmit} className="flex flex-wrap gap-2">
        <label htmlFor="ask-nightshift-input" className="sr-only">
          Ask NightShift a question about your store
        </label>
        <input
          id="ask-nightshift-input"
          type="text"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="e.g. Why did revenue protected go up yesterday?"
          className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:border-gray-500 focus:outline-none"
        />
        <button
          type="submit"
          disabled={isLoading || !question.trim()}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white transition-all duration-150 hover:bg-gray-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLoading ? "Asking…" : "Ask"}
        </button>
      </form>

      {error ? (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      ) : null}

      {answer ? (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <p className="text-sm text-gray-700">{answer}</p>
          {!usedLlm ? (
            <p className="mt-2 text-xs text-gray-400">
              Rule-based summary — AI synthesis hit a temporary limit this time.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
