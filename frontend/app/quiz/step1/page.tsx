'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuizStore } from '@/lib/quiz-store';

const CELEBRITY_PILLS = [
  'David Goggins',
  'Kobe Bryant',
  'Alex Hormozi',
  'Jocko Willink',
  'Naval Ravikant',
  'Andrew Huberman',
];

const TALK_STYLE_PILLS = [
  'Tough love',
  'Gen Z slang',
  'Military style',
  'Funny & casual',
  'Spiritual & mindful',
  'Street smart',
];

const MISS_BEHAVIOR_PILLS = ['Roast me', 'Tough love', 'Be understanding', 'Just move on'];

function Pill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`s1-pill${active ? ' s1-pill--active' : ''}`}
    >
      {label}
    </button>
  );
}

export default function Step1() {
  const router = useRouter();
  const patch = useQuizStore((s) => s.patch);

  const [mode, setMode] = useState<'celebrity' | 'custom' | ''>('');
  const [celebrityName, setCelebrityName] = useState('');
  const [selectedPill, setSelectedPill] = useState('');
  const [personalityDesc, setPersonalityDesc] = useState('');
  const [talkStyle, setTalkStyle] = useState<string[]>([]);
  const [missBehavior, setMissBehavior] = useState('');
  const [intensity, setIntensity] = useState(3);
  const [avoidPhrases, setAvoidPhrases] = useState('');

  const canContinue =
    mode === 'celebrity' ? celebrityName.trim().length > 0
    : mode === 'custom'  ? personalityDesc.trim().length > 0
    : false;

  function handlePillClick(name: string) {
    setSelectedPill(name);
    setCelebrityName(name);
  }

  function handleCelebInput(val: string) {
    setCelebrityName(val);
    if (val !== selectedPill) setSelectedPill('');
  }

  function toggleTalkStyle(s: string) {
    setTalkStyle((prev) => prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]);
  }

  function handleContinue() {
    patch({
      coachSetupMode: mode as 'celebrity' | 'custom',
      celebrityName,
      customCoachPersonalityDesc: personalityDesc,
      coachTalkStyle: talkStyle,
      coachMissBehavior: missBehavior,
      coachIntensity: intensity,
      customCoachAvoidPhrases: avoidPhrases,
    });
    router.push('/quiz/step2');
  }

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

        .s1-root {
          min-height: 100vh;
          background: var(--bg-dark);
          color: var(--text-primary);
          font-family: Inter, sans-serif;
          display: flex;
          flex-direction: column;
        }

        /* ── Top bar ── */
        .s1-topbar {
          padding: 20px 24px;
          font-family: Fredoka, sans-serif;
          font-size: 22px;
          font-weight: 600;
          color: var(--accent-blue);
          border-bottom: 1px solid var(--border);
        }

        /* ── Body ── */
        .s1-body {
          flex: 1;
          display: flex;
          flex-direction: column;
          padding: 32px 20px 40px;
          max-width: 680px;
          width: 100%;
          margin: 0 auto;
        }

        .s1-heading {
          font-family: Fredoka, sans-serif;
          font-size: 28px;
          font-weight: 600;
          margin: 0 0 8px;
          line-height: 1.2;
        }
        .s1-sub {
          color: var(--text-secondary);
          font-size: 15px;
          margin: 0 0 28px;
          line-height: 1.5;
        }

        /* ── Cards ── */
        .s1-cards {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 14px;
          margin-bottom: 20px;
        }
        .s1-card {
          background: var(--bg-card);
          border: 2px solid var(--border);
          border-radius: 14px;
          padding: 20px 16px;
          text-align: left;
          cursor: pointer;
          color: var(--text-primary);
          transition: border-color 0.15s, transform 0.1s;
          width: 100%;
        }
        .s1-card:hover { transform: translateY(-1px); }
        .s1-card--active { border-color: var(--accent-blue); }
        .s1-card-icon { font-size: 28px; margin-bottom: 10px; }
        .s1-card-title { font-weight: 700; font-size: 14px; margin-bottom: 6px; }
        .s1-card-desc { font-size: 12px; color: var(--text-secondary); line-height: 1.55; }

        /* ── Expanded panel ── */
        .s1-panel {
          background: var(--bg-card);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 22px;
          margin-bottom: 24px;
          display: flex;
          flex-direction: column;
          gap: 22px;
        }
        .s1-label {
          font-size: 13px;
          font-weight: 600;
          display: block;
          margin-bottom: 8px;
          color: var(--text-primary);
        }
        .s1-label span { font-weight: 400; color: var(--text-secondary); }
        .s1-input, .s1-textarea {
          width: 100%;
          background: var(--bg-dark);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 10px 12px;
          color: var(--text-primary);
          font-size: 14px;
          font-family: Inter, sans-serif;
          outline: none;
          transition: border-color 0.15s;
        }
        .s1-input:focus, .s1-textarea:focus { border-color: var(--accent-blue); }
        .s1-textarea { resize: vertical; min-height: 80px; }
        .s1-pills { display: flex; flex-wrap: wrap; gap: 8px; }
        .s1-pill {
          padding: 6px 14px;
          border-radius: 20px;
          font-size: 13px;
          font-weight: 500;
          border: 1px solid var(--border);
          background: transparent;
          color: var(--text-secondary);
          cursor: pointer;
          transition: all 0.12s;
          font-family: Inter, sans-serif;
        }
        .s1-pill:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
        .s1-pill--active {
          border-color: var(--accent-blue);
          background: rgba(77,163,255,0.12);
          color: var(--accent-blue);
        }
        .s1-slider { width: 100%; accent-color: var(--accent-blue); }
        .s1-slider-labels {
          display: flex;
          justify-content: space-between;
          font-size: 11px;
          color: var(--text-secondary);
          margin-top: 4px;
        }

        /* ── Continue button ── */
        .s1-cta {
          width: 100%;
          padding: 16px;
          border-radius: 12px;
          font-family: Nunito, sans-serif;
          font-weight: 800;
          font-size: 16px;
          border: none;
          cursor: pointer;
          transition: background 0.15s, opacity 0.15s;
          margin-top: auto;
        }
        .s1-cta--on  { background: var(--accent-blue); color: #fff; }
        .s1-cta--off { background: var(--border); color: var(--text-secondary); cursor: not-allowed; }

        /* ── Desktop layout ── */
        @media (min-width: 900px) {
          .s1-root { flex-direction: row; }

          .s1-topbar {
            display: none; /* moved into sidebar */
          }

          .s1-sidebar {
            width: 360px;
            min-height: 100vh;
            flex-shrink: 0;
            background: #0d1219;
            border-right: 1px solid var(--border);
            padding: 48px 40px;
            display: flex;
            flex-direction: column;
            justify-content: center;
          }
          .s1-sidebar-logo {
            font-family: Fredoka, sans-serif;
            font-size: 24px;
            font-weight: 600;
            color: var(--accent-blue);
            margin-bottom: 48px;
          }
          .s1-sidebar-heading {
            font-family: Fredoka, sans-serif;
            font-size: 36px;
            font-weight: 600;
            line-height: 1.15;
            margin-bottom: 16px;
            color: var(--text-primary);
          }
          .s1-sidebar-sub {
            font-size: 15px;
            color: var(--text-secondary);
            line-height: 1.6;
            margin-bottom: 40px;
          }
          .s1-steps {
            display: flex;
            flex-direction: column;
            gap: 14px;
          }
          .s1-step {
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 13px;
            color: var(--text-secondary);
          }
          .s1-step-dot {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            border: 2px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 700;
            flex-shrink: 0;
          }
          .s1-step-dot--active {
            border-color: var(--accent-blue);
            background: rgba(77,163,255,0.15);
            color: var(--accent-blue);
          }

          .s1-body {
            flex: 1;
            max-width: none;
            padding: 48px 56px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
          }

          .s1-heading { font-size: 34px; }
          .s1-sub { font-size: 16px; margin-bottom: 32px; }

          .s1-cards { gap: 18px; margin-bottom: 24px; }
          .s1-card { padding: 24px 20px; }
          .s1-card-icon { font-size: 32px; }
          .s1-card-title { font-size: 15px; }
          .s1-card-desc { font-size: 13px; }

          .s1-panel { padding: 28px; gap: 24px; }
          .s1-label { font-size: 14px; }
          .s1-input, .s1-textarea { font-size: 15px; padding: 12px 14px; }

          .s1-cta { font-size: 17px; padding: 18px; }
        }

        /* ── Mobile heading hidden (shown in sidebar on desktop) ── */
        .s1-sidebar { display: none; }
        @media (min-width: 900px) {
          .s1-sidebar { display: flex; }
          .s1-mobile-header { display: none; }
        }
      `}</style>

      <div className="s1-root">
        {/* Desktop sidebar */}
        <aside className="s1-sidebar">
          <div className="s1-sidebar-logo">stackd</div>
          <div className="s1-sidebar-heading">How should your coach talk?</div>
          <div className="s1-sidebar-sub">
            This is what makes stackd different — your coach sounds exactly how you want.
          </div>
          <div className="s1-steps">
            {[
              { n: 1, label: 'Choose coach style', active: true },
              { n: 2, label: 'Create account',     active: false },
            ].map(({ n, label, active }) => (
              <div key={n} className="s1-step">
                <div className={`s1-step-dot${active ? ' s1-step-dot--active' : ''}`}>{n}</div>
                <span style={{ color: active ? 'var(--text-primary)' : undefined }}>{label}</span>
              </div>
            ))}
          </div>
        </aside>

        {/* Main content */}
        <main className="s1-body">
          {/* Mobile-only header */}
          <div className="s1-mobile-header" style={{ marginBottom: 24 }}>
            <div style={{ fontFamily: 'Fredoka, sans-serif', fontSize: 22, fontWeight: 600, color: 'var(--accent-blue)', marginBottom: 28 }}>
              stackd
            </div>
            <h1 className="s1-heading">How should your coach talk?</h1>
            <p className="s1-sub">This is what makes stackd different — your coach sounds exactly how you want.</p>
          </div>

          {/* Desktop-only heading (shown above cards) */}
          <div style={{ display: 'none' }} className="s1-desktop-heading">
            <h1 className="s1-heading">Pick your coaching style</h1>
            <p className="s1-sub" style={{ marginBottom: 32 }}>Choose how you want your AI coach to communicate with you.</p>
          </div>

          {/* Cards */}
          <div className="s1-cards">
            <button
              className={`s1-card${mode === 'celebrity' ? ' s1-card--active' : ''}`}
              onClick={() => setMode('celebrity')}
            >
              <div className="s1-card-icon">🌟</div>
              <div className="s1-card-title">Sound like someone famous</div>
              <div className="s1-card-desc">Pick a public figure — your coach will match their style and intensity.</div>
            </button>
            <button
              className={`s1-card${mode === 'custom' ? ' s1-card--active' : ''}`}
              onClick={() => setMode('custom')}
            >
              <div className="s1-card-icon">✏️</div>
              <div className="s1-card-title">Build your own</div>
              <div className="s1-card-desc">Describe exactly how you want your coach to sound and act.</div>
            </button>
          </div>

          {/* Celebrity panel */}
          {mode === 'celebrity' && (
            <div className="s1-panel">
              <div>
                <label className="s1-label">Who should your coach sound like?</label>
                <input
                  className="s1-input"
                  value={celebrityName}
                  onChange={(e) => handleCelebInput(e.target.value)}
                  placeholder="Type a name…"
                  style={{ marginBottom: 14 }}
                />
                <div className="s1-pills">
                  {CELEBRITY_PILLS.map((name) => (
                    <Pill key={name} label={name} active={selectedPill === name} onClick={() => handlePillClick(name)} />
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Custom panel */}
          {mode === 'custom' && (
            <div className="s1-panel">
              <div>
                <label className="s1-label">Describe your coach's personality</label>
                <textarea
                  className="s1-textarea"
                  value={personalityDesc}
                  onChange={(e) => setPersonalityDesc(e.target.value)}
                  placeholder="e.g. Brutally honest, no sugarcoating. Pushes me hard but celebrates wins. Talks like a close friend who's been through it."
                  rows={3}
                />
              </div>

              <div>
                <label className="s1-label">Talk style <span>(pick any)</span></label>
                <div className="s1-pills">
                  {TALK_STYLE_PILLS.map((s) => (
                    <Pill key={s} label={s} active={talkStyle.includes(s)} onClick={() => toggleTalkStyle(s)} />
                  ))}
                </div>
              </div>

              <div>
                <label className="s1-label">When you miss a day, they should…</label>
                <div className="s1-pills">
                  {MISS_BEHAVIOR_PILLS.map((s) => (
                    <Pill key={s} label={s} active={missBehavior === s} onClick={() => setMissBehavior(s)} />
                  ))}
                </div>
              </div>

              <div>
                <label className="s1-label">
                  Intensity: <span style={{ color: 'var(--accent-blue)', fontWeight: 700 }}>{intensity}/5</span>
                </label>
                <input
                  type="range"
                  className="s1-slider"
                  min={1} max={5}
                  value={intensity}
                  aria-valuemin={1}
                  aria-valuemax={5}
                  aria-valuenow={intensity}
                  aria-label={`Coach intensity: ${intensity} out of 5`}
                  onChange={(e) => setIntensity(Number(e.target.value))}
                />
                <div className="s1-slider-labels"><span>Chill</span><span>No mercy</span></div>
              </div>

              <div>
                <label className="s1-label">What should they never do? <span>(optional)</span></label>
                <input
                  className="s1-input"
                  value={avoidPhrases}
                  onChange={(e) => setAvoidPhrases(e.target.value)}
                  placeholder="e.g. Never say 'you got this', no toxic positivity"
                />
              </div>
            </div>
          )}

          <button
            onClick={handleContinue}
            disabled={!canContinue}
            className={`s1-cta ${canContinue ? 's1-cta--on' : 's1-cta--off'}`}
          >
            Continue →
          </button>
        </main>
      </div>
    </>
  );
}
