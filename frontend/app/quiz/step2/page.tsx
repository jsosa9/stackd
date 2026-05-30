'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useQuizStore } from '@/lib/quiz-store';
import { signInWithGoogle } from '@/lib/auth';

export default function Step2() {
  const router = useRouter();
  const data = useQuizStore((s) => s.data);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!data.coachSetupMode) router.replace('/quiz/step1');
  }, [data.coachSetupMode, router]);

  async function handleSignIn() {
    setLoading(true);
    await signInWithGoogle();
  }

  const isCelebrity = data.coachSetupMode === 'celebrity';

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

        .s2-root {
          min-height: 100vh;
          background: var(--bg-dark);
          color: var(--text-primary);
          font-family: Inter, sans-serif;
          display: flex;
          flex-direction: column;
        }

        /* ── Mobile: stacked single column ── */
        .s2-back {
          background: none;
          border: none;
          color: var(--text-secondary);
          font-size: 14px;
          cursor: pointer;
          padding: 20px 20px 0;
          display: flex;
          align-items: center;
          gap: 6px;
          font-family: Inter, sans-serif;
        }

        .s2-inner {
          flex: 1;
          padding: 28px 20px 48px;
          max-width: 520px;
          width: 100%;
          margin: 0 auto;
          display: flex;
          flex-direction: column;
        }

        .s2-badge-wrap { margin-bottom: 24px; }
        .s2-badge {
          display: inline-block;
          border-radius: 20px;
          padding: 6px 14px;
          font-size: 13px;
          font-weight: 600;
        }
        .s2-badge--green {
          background: rgba(61,220,151,0.12);
          color: var(--success-green);
          border: 1px solid rgba(61,220,151,0.3);
        }
        .s2-badge--blue {
          background: rgba(77,163,255,0.12);
          color: var(--accent-blue);
          border: 1px solid rgba(77,163,255,0.3);
        }

        .s2-heading {
          font-family: Fredoka, sans-serif;
          font-size: 28px;
          font-weight: 600;
          margin: 0 0 8px;
          line-height: 1.2;
        }
        .s2-sub {
          color: var(--text-secondary);
          font-size: 15px;
          margin: 0 0 20px;
          line-height: 1.5;
        }

        .s2-reason {
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 16px;
          margin-bottom: 20px;
          font-size: 14px;
          color: var(--text-secondary);
          line-height: 1.65;
        }

        .s2-features {
          list-style: none;
          padding: 0;
          margin: 0 0 20px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .s2-feature {
          display: flex;
          align-items: center;
          gap: 10px;
          font-size: 14px;
          color: var(--text-secondary);
        }
        .s2-feature-check { color: var(--success-green); font-size: 16px; }

        .s2-pro-note {
          font-size: 12px;
          color: var(--text-secondary);
          margin-bottom: 28px;
        }

        .s2-google-btn {
          width: 100%;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 12px;
          background: #fff;
          color: #1a1a1a;
          border: 1px solid #d1d5db;
          border-radius: 12px;
          padding: 14px 20px;
          font-size: 15px;
          font-weight: 600;
          cursor: pointer;
          box-shadow: 0 1px 4px rgba(0,0,0,0.18);
          margin-bottom: 14px;
          font-family: Inter, sans-serif;
          transition: box-shadow 0.15s, opacity 0.15s;
        }
        .s2-google-btn:hover { box-shadow: 0 3px 10px rgba(0,0,0,0.22); }
        .s2-google-btn:disabled { opacity: 0.65; cursor: not-allowed; }

        .s2-spinner {
          width: 18px; height: 18px;
          border: 2px solid #d1d5db;
          border-top-color: #4DA3FF;
          border-radius: 50%;
          display: inline-block;
          animation: spin 0.7s linear infinite;
        }

        .s2-footer {
          font-size: 11px;
          color: var(--text-secondary);
          text-align: center;
        }

        /* ── Desktop: two-column ── */
        @media (min-width: 900px) {
          .s2-root { flex-direction: row; }
          .s2-back { display: none; }

          .s2-sidebar {
            width: 380px;
            min-height: 100vh;
            flex-shrink: 0;
            background: #0d1219;
            border-right: 1px solid var(--border);
            padding: 48px 40px;
            display: flex;
            flex-direction: column;
            justify-content: center;
          }
          .s2-sidebar-logo {
            font-family: Fredoka, sans-serif;
            font-size: 24px;
            font-weight: 600;
            color: var(--accent-blue);
            margin-bottom: 48px;
          }
          .s2-sidebar-heading {
            font-family: Fredoka, sans-serif;
            font-size: 34px;
            font-weight: 600;
            line-height: 1.15;
            margin-bottom: 16px;
            color: var(--text-primary);
          }
          .s2-sidebar-sub {
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.6;
            margin-bottom: 40px;
          }
          .s2-steps {
            display: flex;
            flex-direction: column;
            gap: 14px;
          }
          .s2-step {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 13px;
          }
          .s2-step-dot {
            width: 28px; height: 28px;
            border-radius: 50%;
            border: 2px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 700;
            flex-shrink: 0;
            color: var(--text-secondary);
          }
          .s2-step-dot--done {
            border-color: var(--success-green);
            background: rgba(61,220,151,0.12);
            color: var(--success-green);
          }
          .s2-step-dot--active {
            border-color: var(--accent-blue);
            background: rgba(77,163,255,0.15);
            color: var(--accent-blue);
          }

          .s2-main {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 48px 56px;
            overflow-y: auto;
          }

          .s2-inner {
            max-width: 460px;
            padding: 0;
            margin: 0;
          }

          .s2-heading { font-size: 32px; }
          .s2-sub { font-size: 16px; }
          .s2-google-btn { padding: 16px 24px; font-size: 16px; }

          .s2-back-desktop {
            display: flex;
            align-items: center;
            gap: 6px;
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 13px;
            cursor: pointer;
            padding: 0;
            margin-bottom: 28px;
            font-family: Inter, sans-serif;
          }
          .s2-back-desktop:hover { color: var(--text-primary); }
        }

        .s2-sidebar { display: none; }
        .s2-main { flex: 1; display: flex; flex-direction: column; }
        .s2-back-desktop { display: none; }

        @media (min-width: 900px) {
          .s2-sidebar { display: flex; flex-direction: column; }
          .s2-main { align-items: center; justify-content: center; }
        }

        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>

      <div className="s2-root">
        {/* Desktop sidebar */}
        <aside className="s2-sidebar">
          <div className="s2-sidebar-logo">stackd</div>
          <div className="s2-sidebar-heading">Almost there</div>
          <div className="s2-sidebar-sub">
            Create your account to save your coach and unlock your dashboard.
          </div>
          <div className="s2-steps">
            {[
              { n: '✓', label: 'Coach style chosen', state: 'done' },
              { n: '2',  label: 'Create account',    state: 'active' },
            ].map(({ n, label, state }) => (
              <div key={label} className="s2-step">
                <div className={`s2-step-dot s2-step-dot--${state}`}>{n}</div>
                <span style={{ color: state === 'active' ? 'var(--text-primary)' : 'var(--text-secondary)' }}>{label}</span>
              </div>
            ))}
          </div>
        </aside>

        {/* Mobile back */}
        <button className="s2-back" onClick={() => router.push('/quiz/step1')}>← Back</button>

        {/* Main */}
        <div className="s2-main">
          <div className="s2-inner">
            {/* Desktop back */}
            <button className="s2-back-desktop" onClick={() => router.push('/quiz/step1')}>← Back to coach style</button>

            <div className="s2-badge-wrap">
              <span className={`s2-badge ${isCelebrity ? 's2-badge--green' : 's2-badge--blue'}`}>
                {isCelebrity ? `🌟 Your coach sounds like ${data.celebrityName}` : '✏️ Custom coach'}
              </span>
            </div>

            <h1 className="s2-heading">Create your account</h1>
            <p className="s2-sub">Sign in with Google to activate your coach and access your dashboard.</p>

            <div className="s2-reason">
              Sign in to save your coach settings and access your progress dashboard.
              <br />
              No account = no way to update your coach or track your progress later.
            </div>

            <ul className="s2-features">
              {['Track your streaks and progress', 'See your goal completion over time', 'Manage your coach settings anytime'].map((item) => (
                <li key={item} className="s2-feature">
                  <span className="s2-feature-check">✓</span>
                  {item}
                </li>
              ))}
            </ul>

            <p className="s2-pro-note">Dashboard coming soon.</p>

            <button onClick={handleSignIn} disabled={loading} aria-busy={loading} className="s2-google-btn">
              {loading ? (
                <span className="s2-spinner" role="status" aria-label="Signing in" />
              ) : (
                <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
                  <path fill="#4285F4" d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" />
                  <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.909-2.258c-.806.54-1.837.86-3.047.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 009 18z" />
                  <path fill="#FBBC05" d="M3.964 10.707A5.41 5.41 0 013.682 9c0-.593.102-1.17.282-1.707V4.961H.957A8.996 8.996 0 000 9c0 1.452.348 2.827.957 4.039l3.007-2.332z" />
                  <path fill="#EA4335" d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 00.957 4.961L3.964 7.293C4.672 5.163 6.656 3.58 9 3.58z" />
                </svg>
              )}
              {loading ? 'Signing in…' : 'Continue with Google'}
            </button>

            <p className="s2-footer">By continuing you agree to our Terms and Privacy Policy.</p>
          </div>
        </div>
      </div>
    </>
  );
}
