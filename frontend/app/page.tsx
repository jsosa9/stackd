'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';

const SMS_URL = `sms:${process.env.NEXT_PUBLIC_TWILIO_NUMBER || '+15550000000'}&body=Hello`;

const SMS_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M16 3H4a2 2 0 00-2 2v10a2 2 0 002 2h9l4 4v-4h2a2 2 0 002-2V5a2 2 0 00-2-2z" />
  </svg>
);

const BUBBLES = [
  { type: 'coach', text: 'Who told you this would be easy.' },
  { type: 'user',  text: 'no one' },
  { type: 'coach', text: 'Good. What are we fixing.' },
  { type: 'user',  text: 'I want to run every day' },
  { type: 'coach', text: 'What time tomorrow. I will be there.' },
  { type: 'user',  text: '7am' },
  { type: 'coach', text: 'Set. Do not make me wait.' },
];

const STEPS = [
  {
    num: '01',
    title: <>Text the number.<br /><em>Done.</em></>,
    desc: "No download. No account. Just text and you're in.",
  },
  {
    num: '02',
    title: <>Name your <em>inspiration.</em></>,
    desc: 'An athlete, a founder, an artist. Whoever drives you to be better.',
  },
  {
    num: '03',
    title: <>Your coach <em>shows up.</em></>,
    desc: 'Every morning they text you first. You just reply.',
  },
];

const FEATURES_NOW = [
  { name: 'Goal tracking',     desc: 'Check in on any goal. Each one gets its own daily check-in.' },
  { name: 'Streak building',   desc: 'Every check-in grows your streak. Miss one and your coach notices.' },
  { name: 'Nutrition logging', desc: 'Text what you ate. Calories logged instantly in character.' },
  { name: 'Journal',           desc: 'Vent, reflect, share. Your coach listens and responds.' },
  { name: 'Reminders',         desc: "Text \"remind me at 6pm\" and it's set. Coach follows up." },
  { name: 'Social bets',       desc: 'Make a bet with a friend. Your coach holds you to it.' },
  { name: 'Switch coaches',    desc: 'Text an 8-character code to swap personalities instantly.' },
  { name: 'Morning kickstart', desc: 'Optional motivational text before your day starts.' },
];

const FEATURES_SOON = [
  { name: 'Photo calories',       desc: 'Snap your food. AI estimates calories automatically.' },
  { name: 'Calendar sync',        desc: 'Coach sees your calendar and reminds you before events.' },
  { name: 'Pattern learning',     desc: 'Coach learns your schedule and adapts check-in times.' },
  { name: 'Dashboard',            desc: 'See your streaks, logs, journal, and progress over time.' },
  { name: 'Custom coach',         desc: 'Build a coach from scratch. Tone, phrases, personality.' },
  { name: 'Workout plans',        desc: 'Coach builds a weekly plan and checks each session.' },
  { name: 'Group accountability', desc: 'Add friends to the same coach. Push each other.' },
  { name: 'Integrations',         desc: 'Connect your fitness tracker, sleep data, and more.' },
];

const CARDS = [
  { init: 'NS', name: 'The Navy SEAL',              text: '7AM. Gym day. You going.' },
  { init: 'SO', name: 'The Special Ops Commander',  text: 'Did you read today. No excuses.' },
  { init: 'PS', name: 'The Pop Star',               text: 'Hey did you meditate this morning.' },
  { init: 'VC', name: 'The Venture Capitalist',     text: 'Nutrition check. What did you eat today.' },
];

const COACHES = [
  { i: 'NS', n: 'Navy SEAL. 60+ ultramarathons.',             q: '"Your mind gives up before your body does."' },
  { i: 'ST', n: 'Special ops commander. Author.',             q: '"Discipline equals freedom."' },
  { i: 'BA', n: 'Built a $100M gym brand from zero.',         q: '"The market does not care about your feelings."' },
  { i: 'PS', n: 'Pop star. 100M+ records sold.',              q: '"Showing up is the whole battle."' },
  { i: 'VC', n: 'Invested in Facebook, Twitter, Uber.',       q: '"Self-awareness is the only advantage."' },
  { i: 'MB', n: '5 NBA championships. Known for the Mamba mindset.', q: '"Rest at the end, not in the middle."' },
  { i: 'AN', n: 'Naval officer turned philosopher investor.', q: '"Play long term games with long term people."' },
  { i: 'SN', n: 'Stanford neuroscientist. Optimize everything.', q: '"Your biology is not your destiny."' },
  { i: 'TR', n: 'Coached presidents, athletes, and billionaires.', q: '"The quality of your life is the quality of your questions."' },
  { i: 'OW', n: 'Grew up with nothing. Built a media empire.', q: '"You become what you believe."' },
  { i: 'EM', n: 'Founded 3 companies worth over $1 trillion total.', q: '"Work like the world depends on it."' },
  { i: 'SW', n: '23 Grand Slam titles. Greatest of all time.', q: '"A champion is defined by how they respond to failure."' },
  { i: 'LJ', n: 'From Akron to 4 NBA titles.',                q: '"Nothing is given. Everything is earned."' },
  { i: 'JR', n: 'Comedian. Podcaster. Black belt.',           q: '"Be the hero of your own story."' },
  { i: 'TF', n: 'Author. Early investor in Uber, Facebook, Twitter.', q: '"Focus on being productive not busy."' },
  { i: 'RH', n: 'Bestselling author. Studied Stoic philosophy.', q: '"The obstacle is the way."' },
  { i: 'BG', n: 'Built the most valuable company in history.', q: '"Focus beats talent every single time."' },
  { i: 'BY', n: 'Grammy winner. Built a billion dollar brand.', q: '"Always put yourself first."' },
  { i: 'KJ', n: 'Model. Entrepreneur. 300M+ followers.',      q: '"Quiet work. Loud results."' },
  { i: 'AG', n: 'Pop star. Overcame public scrutiny to thrive.', q: '"Keep going. That is literally all you have to do."' },
];

export default function LandingPage() {
  const navRef      = useRef<HTMLElement>(null);
  const trackRef    = useRef<HTMLDivElement>(null);
  const stackRef    = useRef<HTMLDivElement>(null);
  const [featPage, setFeatPage] = useState(0);

  useEffect(() => {
    // ── Navbar scroll hide/show ──────────────────────────────────────
    let lastY = 0;
    const onScroll = () => {
      const y = window.scrollY;
      if (navRef.current) {
        if (y > lastY && y > 80) {
          navRef.current.classList.add('hidden');
        } else {
          navRef.current.classList.remove('hidden');
        }
      }
      lastY = y;
    };
    window.addEventListener('scroll', onScroll, { passive: true });

    // ── Scroll reveal ────────────────────────────────────────────────
    const revEls = document.querySelectorAll<HTMLElement>('.reveal');
    const revObs = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) e.target.classList.add('on'); }),
      { threshold: 0.10 }
    );
    revEls.forEach((el) => revObs.observe(el));

    // ── Features pager — touch/mouse swipe + arrow keys ─────────────
    const pager = document.querySelector<HTMLElement>('.lp-features-pager');
    let page = 0;
    const total = 2;

    const goPage = (idx: number) => {
      page = ((idx % total) + total) % total;
      setFeatPage(page);
      if (trackRef.current) trackRef.current.style.transform = `translateX(-${page * 100}%)`;
    };

    let fStartX = 0, fDx = 0, fDragging = false;

    const fTouchStart = (e: TouchEvent) => { fStartX = e.touches[0].clientX; fDx = 0; fDragging = true; };
    const fTouchMove  = (e: TouchEvent) => {
      if (!fDragging) return;
      fDx = e.touches[0].clientX - fStartX;
      if (Math.abs(fDx) > 8) e.preventDefault();
    };
    const fTouchEnd   = () => { if (fDragging && Math.abs(fDx) > 30) goPage(page + (fDx < 0 ? 1 : -1)); fDragging = false; };

    const fMouseDown  = (e: MouseEvent) => { fStartX = e.clientX; fDx = 0; fDragging = true; };
    const fMouseMove  = (e: MouseEvent) => { if (fDragging) fDx = e.clientX - fStartX; };
    const fMouseUp    = () => { if (fDragging && Math.abs(fDx) > 30) goPage(page + (fDx < 0 ? 1 : -1)); fDragging = false; };

    const onKeyDown = (e: KeyboardEvent) => {
      if (!pager) return;
      const r = pager.getBoundingClientRect();
      if (r.top > window.innerHeight || r.bottom < 0) return;
      if (e.key === 'ArrowRight') goPage(page + 1);
      if (e.key === 'ArrowLeft')  goPage(page - 1);
    };
    if (pager) {
      pager.addEventListener('touchstart', fTouchStart, { passive: true });
      pager.addEventListener('touchmove',  fTouchMove,  { passive: false });
      pager.addEventListener('touchend',   fTouchEnd);
      pager.addEventListener('mousedown',  fMouseDown);
      pager.addEventListener('mousemove',  fMouseMove);
      pager.addEventListener('mouseup',    fMouseUp);
      pager.addEventListener('mouseleave', fMouseUp);
    }
    document.addEventListener('keydown', onKeyDown);

    // ── Card stack ───────────────────────────────────────────────────
    const stack = stackRef.current;
    if (stack) {
      const cards = Array.from(stack.querySelectorAll<HTMLElement>('.lp-sms-card'));
      let order = [0, 1, 2, 3];
      let busy = false;

      const render = (animated: boolean) => {
        cards.forEach((c, i) => {
          const pos = order.indexOf(i);
          if (!animated) c.style.transition = 'none';
          c.dataset.pos = pos >= 0 && pos <= 3 ? String(pos) : '3';
        });
        if (!animated) {
          stack.offsetHeight; // force reflow
          cards.forEach((c) => (c.style.transition = ''));
        }
      };

      const dismiss = () => {
        if (busy) return;
        busy = true;
        const frontIdx = order[0];
        const frontCard = cards[frontIdx];
        frontCard.dataset.pos = 'out';
        setTimeout(() => {
          order = [...order.slice(1), frontIdx];
          frontCard.style.transition = 'none';
          frontCard.dataset.pos = '3';
          frontCard.offsetHeight; // force reflow
          frontCard.style.transition = '';
          render(true);
          busy = false;
        }, 420);
      };

      let touchStartX = 0, touchStartY = 0;
      const onStackTouchStart = (e: TouchEvent) => {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
      };
      const onStackTouchEnd = (e: TouchEvent) => {
        const dx = e.changedTouches[0].clientX - touchStartX;
        const dy = e.changedTouches[0].clientY - touchStartY;
        if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 36) dismiss();
      };

      stack.addEventListener('click', dismiss);
      stack.addEventListener('touchstart', onStackTouchStart, { passive: true });
      stack.addEventListener('touchend',   onStackTouchEnd);
      render(false);

      return () => {
        window.removeEventListener('scroll', onScroll);
        revObs.disconnect();
        if (pager) {
          pager.removeEventListener('touchstart', fTouchStart);
          pager.removeEventListener('touchmove',  fTouchMove);
          pager.removeEventListener('touchend',   fTouchEnd);
          pager.removeEventListener('mousedown',  fMouseDown);
          pager.removeEventListener('mousemove',  fMouseMove);
          pager.removeEventListener('mouseup',    fMouseUp);
          pager.removeEventListener('mouseleave', fMouseUp);
        }
        document.removeEventListener('keydown', onKeyDown);
        stack.removeEventListener('click', dismiss);
        stack.removeEventListener('touchstart', onStackTouchStart);
        stack.removeEventListener('touchend',   onStackTouchEnd);
      };
    }

    return () => {
      window.removeEventListener('scroll', onScroll);
      revObs.disconnect();
      if (pager) {
        pager.removeEventListener('touchstart', fTouchStart);
        pager.removeEventListener('touchmove',  fTouchMove);
        pager.removeEventListener('touchend',   fTouchEnd);
        pager.removeEventListener('mousedown',  fMouseDown);
        pager.removeEventListener('mousemove',  fMouseMove);
        pager.removeEventListener('mouseup',    fMouseUp);
        pager.removeEventListener('mouseleave', fMouseUp);
      }
      document.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  return (
    <div style={{ background: 'var(--lp-cream)', color: 'var(--lp-ink)', fontFamily: 'var(--font-dm-sans), -apple-system, sans-serif' }}>

      {/* ══ NAVBAR ══════════════════════════════════════════════ */}
      <nav ref={navRef} className="lp-nav">
        <span className="lp-nav-logo">stackd</span>
        <a href={SMS_URL} className="lp-nav-pill">
          {SMS_ICON}
          Start now
        </a>
      </nav>

      {/* ══ HERO ════════════════════════════════════════════════ */}
      <section className="lp-hero">
        <div className="lp-hero-inner">
          <div className="lp-badge">
            <span className="lp-badge-new">Free</span>
            7-day trial · no credit card
          </div>
          <h1 className="lp-h1">
            The voice that drives you.<br />
            <em>Texting you every day.</em>
          </h1>
          <p className="lp-sub">Name your inspiration. Share your goals. Get your first text in 60 seconds.</p>
          {/* CTA — inside hero-inner so it sits below text on desktop */}
          <div className="lp-hero-cta">
            <a href={SMS_URL} className="lp-btn-dark">
              {SMS_ICON}
              Get Started
            </a>
            <p style={{ fontSize: 12, color: 'rgba(26,22,18,0.33)', marginTop: 12 }}>No app. No login. Just text.</p>
          </div>
        </div>

        {/* Phone mockup */}
        <div className="lp-phone-wrap">
          <div className="lp-phone-frame">
            <div className="lp-island" />
            <div className="lp-phone-inner">
              <div className="lp-phone-status">
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--lp-ink)' }}>9:41</span>
                <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                  {[0,1,2].map(i => <span key={i} style={{ display: 'block', width: 4, height: 4, borderRadius: '50%', background: 'var(--lp-ink)' }} />)}
                </div>
              </div>
              <div className="lp-phone-hdr">
                <span style={{ fontSize: 20, color: 'var(--lp-tan)', lineHeight: 1, width: 18, fontWeight: 300 }}>‹</span>
                <div style={{ width: 34, height: 34, borderRadius: '50%', background: 'linear-gradient(135deg,#C8A97A,#8B6A3E)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: 'white', flexShrink: 0 }}>DG</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--lp-ink)' }}>The Navy SEAL</div>
                  <div style={{ fontSize: 10, color: 'var(--lp-muted)' }}>Active now</div>
                </div>
                <div style={{ width: 26, height: 26, borderRadius: '50%', background: 'rgba(26,22,18,0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--lp-ink)" strokeWidth="2.2" aria-hidden="true"><rect x="2" y="7" width="14" height="10" rx="2"/><path d="M16 9l6-2v10l-6-2"/></svg>
                </div>
              </div>
              <div className="lp-phone-msgs">
                {BUBBLES.map((b, i) => (
                  <div key={i} className={`lp-bubble ${b.type}`}>{b.text}</div>
                ))}
              </div>
              <div className="lp-phone-input">
                <div style={{ flex: 1, background: 'rgba(26,22,18,0.06)', borderRadius: 20, padding: '8px 13px', fontSize: 11, color: 'rgba(26,22,18,0.32)' }}>Message</div>
                <div style={{ width: 26, height: 26, borderRadius: '50%', background: 'var(--lp-tan)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="white" aria-hidden="true"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z"/></svg>
                </div>
              </div>
            </div>
          </div>
        </div>

      </section>

      <div className="lp-divider" />

      {/* ══ HOW IT WORKS ════════════════════════════════════════ */}
      <section className="lp-section" style={{ padding: '100px 28px', background: 'var(--lp-warm)' }}>
        <div style={{ maxWidth: 540, margin: '0 auto' }}>
          <span className="lp-eyebrow reveal" style={{ marginBottom: 16, display: 'block' }}>How it works</span>
          <h2 className="lp-h2 reveal" style={{ marginBottom: 52 }}>
            Three texts.<br /><em>You&apos;re live.</em>
          </h2>
          {STEPS.map((s, i) => (
            <div key={i} className={`reveal ${i === 1 ? 'd1' : i === 2 ? 'd2' : ''}`} style={{ display: 'grid', gridTemplateColumns: '50px 1fr', gap: 18, alignItems: 'start', marginBottom: i < STEPS.length - 1 ? 40 : 0 }}>
              <div className="lp-step-num">{s.num}</div>
              <div>
                <h3 className="lp-step-title">{s.title}</h3>
                <p style={{ fontSize: 14, lineHeight: 1.65, color: 'rgba(26,22,18,0.4)' }}>{s.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="lp-divider" />

      {/* ══ FEATURES ════════════════════════════════════════════ */}
      <section className="lp-section" style={{ padding: '100px 28px', background: 'var(--lp-cream)', overflow: 'hidden' }}>
        <div style={{ maxWidth: 540, margin: '0 auto' }}>
          <span className="lp-eyebrow reveal" style={{ marginBottom: 16, display: 'block' }}>Features</span>
          <h2 className="lp-h2 reveal" style={{ marginBottom: 12 }}>Built around <em>your life.</em></h2>
          <p className="reveal" style={{ fontSize: 15, color: 'rgba(26,22,18,0.4)', lineHeight: 1.6, maxWidth: 360, marginBottom: 40 }}>
            Everything happens over text. No app required.
          </p>
          <div className="lp-features-pager reveal">
            <div className="lp-features-track" ref={trackRef}>
              {/* Page 1 */}
              <div className="lp-features-page">
                <h3 style={{ fontFamily: 'var(--font-dm-serif),serif', fontSize: 22, color: 'var(--lp-ink)', marginBottom: 24, letterSpacing: '-0.015em' }}>What your coach does today.</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  {FEATURES_NOW.map((f) => (
                    <div key={f.name} className="lp-feature-card">
                      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--lp-ink)', marginBottom: 6, letterSpacing: '-0.01em' }}>{f.name}</div>
                      <div style={{ fontSize: 12, color: 'rgba(26,22,18,0.46)', lineHeight: 1.45 }}>{f.desc}</div>
                    </div>
                  ))}
                </div>
              </div>
              {/* Page 2 */}
              <div className="lp-features-page">
                <h3 style={{ fontFamily: 'var(--font-dm-serif),serif', fontSize: 22, color: 'var(--lp-ink)', marginBottom: 24, letterSpacing: '-0.015em' }}>What&apos;s coming next.</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  {FEATURES_SOON.map((f) => (
                    <div key={f.name} className="lp-feature-card soon">
                      <span className="lp-feature-pill">Soon</span>
                      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--lp-ink)', marginBottom: 6, letterSpacing: '-0.01em' }}>{f.name}</div>
                      <div style={{ fontSize: 12, color: 'rgba(26,22,18,0.46)', lineHeight: 1.45 }}>{f.desc}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            {/* Dots */}
            <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 32 }}>
              {[0, 1].map((i) => (
                <button
                  key={i}
                  className={`lp-dot ${featPage === i ? 'active' : ''}`}
                  onClick={() => {
                    const newPage = i;
                    setFeatPage(newPage);
                    if (trackRef.current) trackRef.current.style.transform = `translateX(-${newPage * 100}%)`;
                  }}
                  aria-label={`Page ${i + 1}`}
                />
              ))}
            </div>
          </div>
        </div>
      </section>

      <div className="lp-divider" />

      {/* ══ ACCOUNTABILITY CARD STACK ═══════════════════════════ */}
      <section className="lp-section" style={{ padding: '100px 28px', background: 'var(--lp-cream)' }}>
        <div style={{ maxWidth: 520, margin: '0 auto' }}>
          <span className="lp-eyebrow reveal" style={{ marginBottom: 16, display: 'block' }}>Accountability</span>
          <h2 className="lp-h2 reveal" style={{ marginBottom: 12 }}>Your coach is <em>waiting.</em></h2>
          <p className="reveal" style={{ fontSize: 15, color: 'rgba(26,22,18,0.4)', lineHeight: 1.65, maxWidth: 360, marginBottom: 48 }}>
            Real coaches built by real users. Swipe to see what a day looks like.
          </p>
          <div className="lp-card-stack" ref={stackRef} aria-label="Coach message previews">
            {CARDS.map((c, i) => (
              <div
                key={i}
                className="lp-sms-card"
                data-pos={String(i)}
                role="button"
                tabIndex={i === 0 ? 0 : -1}
                aria-label={`Message from ${c.name}: ${c.text}. Press Enter or Space to dismiss.`}
                onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && i === 0) { e.preventDefault(); const card = e.currentTarget as HTMLElement; card.style.transform = 'translateX(120%) rotate(12deg)'; card.style.opacity = '0'; } }}
              >
                <div className="lp-sms-av" aria-hidden="true">{c.init}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'rgba(26,22,18,0.38)', marginBottom: 3 }}>{c.name} · now</div>
                  <div style={{ fontSize: 13, color: 'var(--lp-ink)', lineHeight: 1.4 }}>{c.text}</div>
                  <div style={{ fontSize: 10, color: 'rgba(26,22,18,0.26)', marginTop: 3 }} aria-hidden="true">Slide to reply</div>
                </div>
              </div>
            ))}
          </div>
          <p style={{ fontSize: 12, color: 'rgba(26,22,18,0.35)', marginTop: 24, textAlign: 'center', letterSpacing: '0.02em' }}>
            Tap or swipe up to dismiss
          </p>
        </div>
      </section>

      <div className="lp-divider" />

      {/* ══ COACHES CAROUSEL ════════════════════════════════════ */}
      <section className="lp-section" style={{ padding: '100px 0', background: 'var(--lp-warm)', overflow: 'hidden' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', padding: '0 28px' }}>
          <span className="lp-eyebrow reveal" style={{ marginBottom: 12, display: 'block' }}>You name them. We build them.</span>
          <h2 className="lp-h2 reveal" style={{ marginBottom: 12 }}>Any voice. <em>Any philosophy.</em></h2>
          <p className="reveal" style={{ fontSize: 15, color: 'rgba(26,22,18,0.4)', lineHeight: 1.6, marginBottom: 44 }}>
            Text any name. Athlete, founder, artist, anyone who drives you. We study how they think and build your coach from their philosophy and standards.
          </p>
        </div>
        <div className="lp-marquee">
          <div className="lp-coaches-track">
            {[...COACHES, ...COACHES].map((c, i) => (
              <div key={i} className="lp-coach-pill">
                <div className="lp-coach-init">{c.i}</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--lp-ink)', marginBottom: 6 }}>{c.n}</div>
                <div style={{ fontSize: 12, color: 'var(--lp-muted)', lineHeight: 1.45, fontStyle: 'italic' }}>&ldquo;{c.q}&rdquo;</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="lp-divider" />

      {/* ══ PRICING ═════════════════════════════════════════════ */}
      <section className="lp-section" style={{ padding: '100px 28px', background: 'var(--lp-black)', fontFamily: 'var(--font-dm-sans),sans-serif' }}>
        <div style={{ maxWidth: 400, margin: '0 auto', textAlign: 'center' }}>
          <span className="lp-eyebrow reveal" style={{ color: 'var(--lp-tan)', marginBottom: 14, display: 'block' }}>Pricing</span>
          <h2 className="reveal" style={{ fontFamily: 'var(--font-dm-serif),serif', fontSize: 'clamp(34px,7vw,48px)', lineHeight: 1.05, letterSpacing: '-0.02em', color: 'var(--lp-off-white)', marginBottom: 36 }}>
            One coach.<br />Every day.
          </h2>
          <div className="lp-price-card reveal">
            <div className="lp-price-num">Free</div>
            <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.28)', marginBottom: 24 }}>for 7 days · then $9.99 / month</div>
            <div className="lp-price-hr" />
            <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 11, marginBottom: 28 }}>
              {['Unlimited daily texts', 'Any coach you can name', 'No app, no login', 'Cancel anytime'].map((item) => (
                <li key={item} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 14, color: 'rgba(255,255,255,0.55)' }}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--lp-tan)', flexShrink: 0, display: 'inline-block' }} />
                  {item}
                </li>
              ))}
            </ul>
            <a href={SMS_URL} className="lp-btn-light">
              {SMS_ICON}
              Start free
            </a>
          </div>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.18)' }}>No credit card required to start.</p>
        </div>
      </section>

      {/* ══ FOOTER CTA ══════════════════════════════════════════ */}
      <section className="lp-section" style={{ padding: '120px 28px', textAlign: 'center', background: 'var(--lp-cream)', borderTop: '1px solid rgba(26,22,18,0.08)' }}>
        <h2 className="lp-footer-cta-h2 reveal">
          Your coach<br />is <em>waiting.</em>
        </h2>
        <div className="reveal d1" style={{ display: 'flex', justifyContent: 'center' }}>
          <a href={SMS_URL} className="lp-btn-dark" style={{ maxWidth: 320 }}>
            {SMS_ICON}
            Get Started
          </a>
        </div>
      </section>

      {/* ══ FOOTER ══════════════════════════════════════════════ */}
      <footer style={{ padding: '24px 28px', display: 'flex', flexDirection: 'column', gap: 12, background: 'var(--lp-cream)', borderTop: '1px solid rgba(26,22,18,0.08)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          <span className="lp-footer-logo">stackd</span>
          <span style={{ fontSize: 12, color: 'rgba(26,22,18,0.22)' }}>© 2026 stackd · 7 days free · $9.99/mo after</span>
        </div>
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
          <Link href="/privacy" style={{ fontSize: 12, color: 'rgba(26,22,18,0.35)', textDecoration: 'none' }}>Privacy Policy</Link>
          <Link href="/terms"   style={{ fontSize: 12, color: 'rgba(26,22,18,0.35)', textDecoration: 'none' }}>Terms of Service</Link>
          <Link href="/stop"    style={{ fontSize: 12, color: 'rgba(26,22,18,0.35)', textDecoration: 'none' }}>Unsubscribe</Link>
          <Link href="/help"    style={{ fontSize: 12, color: 'rgba(26,22,18,0.35)', textDecoration: 'none' }}>Help</Link>
          <a href="mailto:support@stackd.chat" style={{ fontSize: 12, color: 'rgba(26,22,18,0.35)', textDecoration: 'none' }}>Contact</a>
        </div>
        <p style={{ fontSize: 11, color: 'rgba(26,22,18,0.22)', lineHeight: 1.5, maxWidth: 480 }}>
          AI coaches are inspired by public figures and are not affiliated with or endorsed by them.
        </p>
      </footer>

    </div>
  );
}
