"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { useQuiz } from "@/lib/quiz-state";
import { finalizeOnboarding, validateOnboarding } from "@/lib/quiz-mapper";

export default function QuizComplete() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const runningRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    runningRef.current = false;

    async function run() {
      if (runningRef.current) return;
      runningRef.current = true;

      const { data: { user }, error: userErr } = await supabase.auth.getUser();
      if (userErr || !user) {
        if (!cancelled) setError("Could not verify your account. Please go back and try again.");
        runningRef.current = false;
        return;
      }

      const quizData = useQuiz.getState().data;
      if (!quizData.coachName && !quizData.stagedGoals?.length) {
        if (!cancelled) router.replace("/app/home");
        return;
      }

      try {
        await finalizeOnboarding(quizData);

        try {
          await validateOnboarding();
        } catch {
          await new Promise(r => setTimeout(r, 800));
          await validateOnboarding();
        }

        useQuiz.getState().clear();
        if (!cancelled) router.replace("/app/home");
      } catch (err) {
        runningRef.current = false;
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Something went wrong saving your data.");
        }
      }
    }

    run();
    return () => { cancelled = true; };
  }, [attempt, router]);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center px-5"
        style={{ background: '#0B0F14' }}>
        <div className="text-center max-w-sm">
          <div className="text-5xl mb-4">😬</div>
          <h1 className="font-heading text-[26px] q-text-primary mb-3">Something went wrong</h1>
          <p className="text-[14px] q-text-muted font-semibold mb-6 leading-[1.6]">{error}</p>
          <div className="flex flex-col gap-3">
            <button
              onClick={() => { setError(""); setAttempt(a => a + 1); }}
              className="q-btn-primary w-full justify-center"
            >
              Try again
            </button>
            <button
              onClick={() => router.push("/quiz/step7")}
              className="q-btn-secondary w-full justify-center"
            >
              ← Back to sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: '#0B0F14' }}>
      <div className="text-center" aria-live="polite" aria-atomic="true">
        <div className="text-6xl mb-4" aria-hidden="true">⚡</div>
        <h1 className="font-heading text-[28px] q-text-primary mb-2">Setting up your coach…</h1>
        <p className="text-[14px] q-text-muted font-semibold">Saving your preferences</p>
        <div
          role="progressbar"
          aria-valuenow={60}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Setup progress"
          className="mt-6 h-1.5 rounded-full overflow-hidden max-w-[200px] mx-auto"
          style={{ background: '#1F2937' }}
        >
          <div className="h-full rounded-full w-3/5 animate-pulse" style={{ background: '#4DA3FF' }} />
        </div>
      </div>
    </div>
  );
}
