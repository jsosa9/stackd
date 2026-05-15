'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '@/lib/supabase';
import { checkUserExists } from '@/lib/auth';
import { useQuizStore } from '@/lib/quiz-store';

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

type Screen = 'loading' | 'ready';

export default function AuthCallback() {
  const router = useRouter();
  const [screen, setScreen] = useState<Screen>('loading');
  const [status, setStatus] = useState('Setting up your coach…');
  const [token, setToken] = useState('');
  const [twilioNumber, setTwilioNumber] = useState('');
  const [coachLabel, setCoachLabel] = useState('');
  const clearQuiz = useQuizStore((s) => s.clear);

  useEffect(() => {
    const run = async () => {
      try {
        let session = null;
        for (let i = 0; i < 4; i++) {
          const { data } = await supabase.auth.getSession();
          if (data.session) { session = data.session; break; }
          await new Promise((r) => setTimeout(r, 500));
        }

        if (!session) { router.push('/'); return; }

        const userId = session.user.id;
        const email  = session.user.email ?? '';
        const name   = session.user.user_metadata?.full_name ?? '';

        const userExists = await checkUserExists(userId);
        if (userExists) { router.push('/dashboard'); return; }

        setStatus('Building your coach…');

        // Read from getState() — avoids stale closure from pre-hydration render
        const {
          coachSetupMode, celebrityName, customCoachPersonalityDesc,
          coachTalkStyle, coachMissBehavior, coachIntensity, customCoachAvoidPhrases,
        } = useQuizStore.getState().data;

        const quizResponse = await fetch(`${API_URL}/api/complete-quiz`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: userId, email, goals: {},
            coach: {
              name: 'Coach',
              personality: coachSetupMode === 'celebrity' ? 'hype' : 'custom',
              setup_type: coachSetupMode,
              sounds_like: celebrityName ?? '',
              custom_build: {
                personality_desc: customCoachPersonalityDesc,
                talk_style: coachTalkStyle,
                miss_behavior: coachMissBehavior,
                intensity: coachIntensity,
                avoid_phrases: customCoachAvoidPhrases,
              },
            },
            about: { name, age: null, occupation: '' },
            boundaries: { off_limits: [] },
            schedule: {
              checkin_hour: 8, checkin_minute: 0, checkin_ampm: 'AM',
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/Los_Angeles',
              motivation_enabled: false,
            },
            phone: null,
          }),
        });
        const quizRes = await quizResponse.json();

        clearQuiz();
        setToken(quizRes.personality_id ?? '');
        setTwilioNumber(quizRes.twilio_number ?? process.env.NEXT_PUBLIC_TWILIO_NUMBER ?? '');
        setCoachLabel(
          coachSetupMode === 'celebrity' && celebrityName
            ? `Inspired by ${celebrityName}`
            : 'Custom coach',
        );
        setScreen('ready');
      } catch (err) {
        console.error('Auth callback error:', err);
        router.push('/');
      }
    };
    run();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const smsHref = `sms:${twilioNumber}${twilioNumber ? `?body=${encodeURIComponent(token)}` : ''}`;

  return (
    <>
      <style>{`
        :root {
          --bg-dark: #0B0F14;
          --bg-card: #121A23;
          --text-primary: #E8EEF4;
          --text-secondary: #6B7A8D;
          --accent-blue: #4DA3FF;
          --success-green: #3DDC97;
          --border: #1E2A35;
        }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--bg-dark); }

        .cb-root {
          min-height: 100vh;
          background: var(--bg-dark);
          color: var(--text-primary);
          font-family: Inter, sans-serif;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 32px 20px;
        }

        /* Loading */
        .cb-loading {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 20px;
          text-align: center;
        }
        .cb-loading-icon { font-size: 52px; animation: pulse 2s ease-in-out infinite; }
        .cb-loading-text { color: #9AA4AF; font-size: 16px; font-weight: 500; }

        /* Ready screen */
        .cb-card {
          width: 100%;
          max-width: 480px;
          text-align: center;
          display: flex;
          flex-direction: column;
          align-items: center;
        }

        .cb-emoji { font-size: 56px; margin-bottom: 20px; }
        .cb-heading {
          font-family: Fredoka, sans-serif;
          font-size: 30px;
          font-weight: 600;
          margin: 0 0 8px;
        }
        .cb-label { color: var(--success-green); font-size: 14px; font-weight: 600; margin-bottom: 8px; }
        .cb-desc {
          color: var(--text-secondary);
          font-size: 14px;
          line-height: 1.65;
          margin-bottom: 32px;
          max-width: 360px;
        }

        .cb-token-card {
          width: 100%;
          background: var(--bg-card);
          border: 1px solid var(--success-green);
          border-radius: 16px;
          padding: 24px 20px;
          margin-bottom: 24px;
        }
        .cb-token-eyebrow {
          font-size: 11px;
          font-weight: 600;
          color: var(--success-green);
          letter-spacing: 0.5px;
          text-transform: uppercase;
          margin-bottom: 8px;
        }
        .cb-token-sub {
          font-size: 13px;
          color: var(--text-secondary);
          margin-bottom: 18px;
        }
        .cb-token {
          font-family: monospace;
          font-size: 36px;
          font-weight: 700;
          color: var(--text-primary);
          letter-spacing: 4px;
          margin-bottom: 12px;
        }
        .cb-token-note { font-size: 12px; color: var(--text-secondary); }

        .cb-btn-primary {
          display: block;
          width: 100%;
          padding: 16px;
          border-radius: 12px;
          background: var(--accent-blue);
          color: #fff;
          font-family: Nunito, sans-serif;
          font-weight: 800;
          font-size: 16px;
          text-decoration: none;
          margin-bottom: 12px;
          text-align: center;
          transition: opacity 0.15s;
        }
        .cb-btn-primary:hover { opacity: 0.9; }

        .cb-btn-secondary {
          width: 100%;
          padding: 14px;
          border-radius: 12px;
          background: transparent;
          border: 1px solid var(--border);
          color: var(--text-secondary);
          font-family: Nunito, sans-serif;
          font-weight: 700;
          font-size: 15px;
          cursor: pointer;
          transition: border-color 0.15s, color 0.15s;
        }
        .cb-btn-secondary:hover { border-color: var(--text-secondary); color: var(--text-primary); }

        /* Desktop enhancements */
        @media (min-width: 768px) {
          .cb-emoji { font-size: 68px; }
          .cb-heading { font-size: 38px; }
          .cb-desc { font-size: 15px; max-width: 400px; }
          .cb-token { font-size: 44px; letter-spacing: 6px; }
          .cb-token-card { padding: 32px 28px; }
          .cb-btn-primary { font-size: 17px; padding: 18px; }
          .cb-card { max-width: 520px; }
        }

        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes spin   { to{transform:rotate(360deg)} }
      `}</style>

      <div className="cb-root">
        {screen === 'loading' ? (
          <div className="cb-loading" aria-live="polite" aria-atomic="true" role="status">
            <div className="cb-loading-icon" aria-hidden="true">🎯</div>
            <p className="cb-loading-text">{status}</p>
          </div>
        ) : (
          <div className="cb-card">
            <div className="cb-emoji" aria-hidden="true">🎉</div>
            <h1 className="cb-heading">Your coach is ready</h1>
            <p className="cb-label">{coachLabel}</p>
            <p className="cb-desc">
              One tap to activate. Your coach will be waiting on the other end.
            </p>

            <div className="cb-token-card">
              <div className="cb-token-eyebrow">Your Personality ID</div>
              <div className="cb-token" aria-label={`Your coach activation code: ${token}`}>{token}</div>
            </div>

            <a href={smsHref} aria-label={`Text your coach to activate — opens SMS app`} className="cb-btn-primary">Text your coach →</a>

            <p className="cb-token-note" style={{ marginTop: '14px', fontSize: '13px' }}>
              Your message will be pre-filled with your ID. Don&apos;t delete it — just hit send and your coach activates instantly.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
