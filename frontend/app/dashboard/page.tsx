'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '@/lib/supabase';

/* ─── STATIC DATA ─── */
const BADGE_DEFS = [
  { emoji: '🌱', label: 'Getting started', streakNeeded: 1  },
  { emoji: '🔥', label: 'One week',         streakNeeded: 7  },
  { emoji: '💪', label: 'Two weeks',        streakNeeded: 14 },
  { emoji: '⚡', label: 'One month',        streakNeeded: 30 },
  { emoji: '🏆', label: 'Two months',       streakNeeded: 60 },
  { emoji: '👑', label: 'Legend',           streakNeeded: 90 },
];

/* ─── TYPES ─── */
interface Goal {
  id: string;
  activity: string;
  category: string;
  days: string[];
  streak: number;
  longest: number;
}

interface Message {
  id: string;
  from: 'coach' | 'user';
  body: string;
  time: string;
}

/* ─── HELPERS ─── */
const CATEGORY_COLORS: Record<string, string> = {
  fitness:  '#4DA3FF',
  learning: '#6366F1',
  wellness: '#F97316',
  career:   '#3B82F6',
};
const CATEGORY_SUBS: Record<string, string> = {
  fitness:  'Build strength and discipline.',
  learning: 'Expand your mind daily.',
  wellness: 'Take care of yourself.',
  career:   'Work on what matters.',
};
function goalColor(cat: string) { return CATEGORY_COLORS[cat] || '#4DA3FF'; }
function goalSub(cat: string)   { return CATEGORY_SUBS[cat]   || 'Stay consistent.'; }

function formatMessageTime(createdAt: string): string {
  const d = new Date(createdAt);
  const now = new Date();
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return `Today, ${time}`;
  const yesterday = new Date(now.getTime() - 86400000);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday, ${time}`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function deriveCheckInPref(checkinTime: string): string {
  const h = parseInt(checkinTime.split(':')[0]) || 8;
  if (h < 12) return 'Morning';
  if (h < 15) return 'Midday';
  return 'Evening';
}

function makeInitials(name: string): string {
  return name.split(' ').map(w => w[0] || '').join('').toUpperCase().slice(0, 2);
}

/* ─── STAT CARD ─── */
function StatCard({ label, value, unit, icon, sub, progress }: {
  label: string; value: string; unit?: string; icon?: string; sub: string; progress?: number;
}) {
  return (
    <div style={{ background: 'var(--bg-card)', borderRadius: 14, padding: 20, border: '1px solid var(--border)' }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.3px', marginBottom: 10, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, marginBottom: 6 }}>
        <span style={{ fontFamily: 'Inter, sans-serif', fontSize: 38, color: 'var(--text-primary)', lineHeight: 1, letterSpacing: '-1px' }}>{value}</span>
        {unit && <span style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 4 }}>{unit}</span>}
        {icon && <span style={{ fontSize: 20, marginBottom: 2, marginLeft: 4 }}>{icon}</span>}
      </div>
      {progress != null && (
        <div style={{ height: 3, background: 'var(--border)', borderRadius: 2, marginBottom: 6 }}>
          <div style={{ height: '100%', width: `${progress}%`, background: 'var(--accent-blue)', borderRadius: 2 }} />
        </div>
      )}
      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{sub}</div>
    </div>
  );
}

/* ─── TOGGLE ─── */
function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      style={{ width: 44, height: 24, borderRadius: 12, background: on ? 'var(--accent-blue)' : 'var(--border)', cursor: 'pointer', position: 'relative', transition: 'background 0.2s', flexShrink: 0, border: 'none', padding: 0 }}
    >
      <div style={{ position: 'absolute', top: 3, left: on ? 23 : 3, width: 18, height: 18, borderRadius: '50%', background: '#FFFFFF', transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.2)' }} />
    </button>
  );
}

/* ─── MAIN COMPONENT ─── */
export default function DashboardPage() {
  const router = useRouter();

  const [activeTab, setActiveTab] = useState<'overview' | 'goals' | 'coach' | 'settings'>('overview');
  const [isMobile, setIsMobile]   = useState(false);
  const [loading,  setLoading]    = useState(true);
  const timeModalId  = 'checkin-time-modal';
  const pauseModalId = 'pause-coach-modal';

  /* auth / user */
  const [userId,       setUserId]       = useState('');
  const [userName,     setUserName]     = useState('');
  const [userInitials, setUserInitials] = useState('');
  const [userPhone,    setUserPhone]    = useState('');

  /* coach */
  const [coachName,  setCoachName]  = useState('Coach Alex');

  /* real data */
  const [goals,    setGoals]    = useState<Goal[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);

  /* modal state */
  const [showTimeModal,  setShowTimeModal]  = useState(false);
  const [showPauseModal, setShowPauseModal] = useState(false);
  const [checkinTime,    setCheckinTime]    = useState('8:00 AM');
  const [coachPaused,    setCoachPaused]    = useState(false);
  const [editingTime,    setEditingTime]    = useState('08:00');

  /* coach tab UI */
  const [coachStyle,  setCoachStyle]  = useState('Motivating');
  const [checkInPref, setCheckInPref] = useState('Morning');
  const [saved,       setSaved]       = useState(false);

  /* settings tab UI */
  const [filter,    setFilter]    = useState('All');
  const [notifs,    setNotifs]    = useState({ morning: true, evening: true, weekly: false });

  /* resize */
  useEffect(() => {
    const handle = () => setIsMobile(window.innerWidth < 768);
    handle();
    window.addEventListener('resize', handle);
    return () => window.removeEventListener('resize', handle);
  }, []);

  /* Escape key closes modals */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowTimeModal(false);
        setShowPauseModal(false);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  /* ── DATA FETCH ── */
  useEffect(() => {
    let channel: ReturnType<typeof supabase.channel> | null = null;

    const fetchData = async () => {
      try {
        /* 1. Auth */
        const { data: { user: authUser } } = await supabase.auth.getUser();
        if (!authUser) { router.push('/'); return; }

        setUserId(authUser.id);
        const rawName = authUser.user_metadata?.full_name
          || authUser.user_metadata?.name
          || authUser.email?.split('@')[0]
          || 'User';
        setUserName(rawName);
        setUserInitials(makeInitials(rawName));

        /* 2. User profile */
        const { data: profile } = await supabase
          .from('users')
          .select('name, phone')
          .eq('id', authUser.id)
          .single();

        if (profile?.name) {
          setUserName(profile.name);
          setUserInitials(makeInitials(profile.name));
        }
        if (profile?.phone) setUserPhone(profile.phone);

        /* 3. Goals */
        const { data: goalsData } = await supabase
          .from('goals')
          .select('*')
          .eq('user_id', authUser.id);

        /* 4. Streaks */
        const { data: streaksData } = await supabase
          .from('streaks')
          .select('*')
          .eq('user_id', authUser.id);

        const mergedGoals: Goal[] = (goalsData || []).map(g => {
          const streak = (streaksData || []).find(s => s.goal_id === g.id);
          return {
            id:       g.id,
            activity: g.activity,
            category: g.category,
            days:     Array.isArray(g.days) ? g.days : [],
            streak:   streak?.current_streak  || 0,
            longest:  streak?.longest_streak  || 0,
          };
        });
        setGoals(mergedGoals);

        /* 5. Messages */
        const { data: messagesData } = await supabase
          .from('messages')
          .select('*')
          .eq('user_id', authUser.id)
          .order('created_at', { ascending: false })
          .limit(20);

        setMessages(
          (messagesData || []).map(m => ({
            id:   m.id,
            from: m.direction === 'inbound' ? 'user' : 'coach',
            body: m.body,
            time: formatMessageTime(m.created_at),
          }))
        );

        /* 6. Coach settings */
        const { data: coachData } = await supabase
          .from('coach_settings')
          .select('*')
          .eq('user_id', authUser.id)
          .single();

        if (coachData) {
          if (coachData.coach_name)       setCoachName(coachData.coach_name);
          if (coachData.personality_preset) setCoachStyle(coachData.personality_preset);
        }

        /* 7. Schedule */
        const { data: scheduleData } = await supabase
          .from('schedule')
          .select('*')
          .eq('user_id', authUser.id)
          .single();

        if (scheduleData) {
          if (scheduleData.checkin_time) {
            setCheckinTime(scheduleData.checkin_time);
            setEditingTime(scheduleData.checkin_time);
            setCheckInPref(deriveCheckInPref(scheduleData.checkin_time));
          }
          if (scheduleData.motivation_enabled != null) {
            setNotifs(n => ({ ...n, morning: scheduleData.motivation_enabled }));
          }
        }

        /* 8. Realtime */
        channel = supabase
          .channel(`messages:${authUser.id}`)
          .on(
            'postgres_changes',
            { event: 'INSERT', schema: 'public', table: 'messages', filter: `user_id=eq.${authUser.id}` },
            payload => {
              const m = payload.new as any;
              setMessages(prev => [
                { id: m.id, from: m.direction === 'inbound' ? 'user' : 'coach', body: m.body, time: formatMessageTime(m.created_at) },
                ...prev.slice(0, 19),
              ]);
            }
          )
          .subscribe();

        setLoading(false);
      } catch (err) {
        console.error('Dashboard fetch error:', err);
        setLoading(false);
      }
    };

    fetchData();
    return () => { channel?.unsubscribe(); };
  }, [router]);

  /* ── DERIVED ── */
  const getGreeting = () => {
    const h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  };

  const currentStreak = goals.length > 0 ? Math.max(...goals.map(g => g.streak)) : 0;
  const bestStreak    = goals.length > 0 ? Math.max(...goals.map(g => g.longest)) : 0;
  const goalsOnTrack  = goals.filter(g => g.streak >= 3).length;
  const progressPct   = goals.length > 0 ? Math.round(goalsOnTrack / goals.length * 100) : 0;

  /* ── SAVE HANDLERS ── */
  const handleSave = async () => {
    if (!userId) return;
    try {
      if (activeTab === 'coach') {
        await fetch('/api/update-coach', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, name: coachName, personality: coachStyle }),
        });
      } else {
        await fetch('/api/update-schedule', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, checkin_time: checkinTime, motivation_enabled: notifs.morning }),
        });
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      console.error('Save error:', err);
    }
  };

  const handleSaveTime = async () => {
    setCheckinTime(editingTime);
    setShowTimeModal(false);
    if (userId) {
      try {
        await fetch('/api/update-schedule', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, checkin_time: editingTime }),
        });
      } catch (err) {
        console.error('Time save error:', err);
      }
    }
  };

  const handlePauseToggle = async () => {
    const newPaused = !coachPaused;
    setCoachPaused(newPaused);
    setShowPauseModal(false);
    if (userId) {
      try {
        await fetch('/api/pause-coach', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: userId, paused: newPaused }),
        });
      } catch (err) {
        console.error('Pause error:', err);
      }
    }
  };

  /* ── LOADING ── */
  if (loading) {
    return (
      <div style={{ display: 'flex', height: '100vh', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-dark)', fontFamily: 'Inter, sans-serif' }}>
        <style>{`@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'); @keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <div style={{ textAlign: 'center' }} role="status" aria-label="Loading dashboard">
          <div style={{ width: 40, height: 40, borderRadius: '50%', border: '3px solid var(--border)', borderTopColor: 'var(--accent-blue)', animation: 'spin 0.8s linear infinite', margin: '0 auto 16px' }} aria-hidden="true" />
          <div style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Loading your dashboard…</div>
        </div>
      </div>
    );
  }

  /* ── SHARED MODALS ── */
  const modals = (
    <>
      {showTimeModal && (
        <>
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(11, 15, 20, 0.6)', zIndex: 40 }} onClick={() => setShowTimeModal(false)} aria-hidden="true" />
          <div role="dialog" aria-modal="true" aria-labelledby={timeModalId} style={{ position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)', width: '100%', maxWidth: 480, background: 'var(--bg-card)', borderTop: '1px solid var(--border)', borderRadius: '20px 20px 0 0', padding: 24, zIndex: 50 }}>
            <div id={timeModalId} style={{ fontFamily: 'Inter, sans-serif', fontSize: 20, color: 'var(--text-primary)', marginBottom: 16 }}>Check-in Time</div>
            <input type="time" value={editingTime} onChange={e => setEditingTime(e.target.value)}
              style={{ width: '100%', border: '1.5px solid var(--border)', borderRadius: 9, padding: '10px 14px', fontFamily: 'Inter, sans-serif', fontSize: 20, color: 'var(--text-primary)', background: 'rgba(77, 163, 255, 0.1)', outline: 'none', marginBottom: 16 }}
            />
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setShowTimeModal(false)} style={{ flex: 1, background: 'var(--bg-dark)', border: '1px solid var(--border)', borderRadius: 9, padding: '10px', fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)', cursor: 'pointer' }}>Cancel</button>
              <button onClick={handleSaveTime} style={{ flex: 1, background: 'var(--accent-blue)', border: 'none', borderRadius: 9, padding: '10px', fontSize: 14, fontWeight: 600, color: 'var(--bg-dark)', cursor: 'pointer' }}>Save</button>
            </div>
          </div>
        </>
      )}
      {showPauseModal && (
        <>
          <div style={{ position: 'fixed', inset: 0, background: 'rgba(11, 15, 20, 0.6)', zIndex: 40 }} onClick={() => setShowPauseModal(false)} aria-hidden="true" />
          <div role="dialog" aria-modal="true" aria-labelledby={pauseModalId} style={{ position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)', width: '100%', maxWidth: 480, background: 'var(--bg-card)', borderTop: '1px solid var(--border)', borderRadius: '20px 20px 0 0', padding: 24, zIndex: 50 }}>
            <div id={pauseModalId} style={{ fontFamily: 'Inter, sans-serif', fontSize: 20, color: 'var(--text-primary)', marginBottom: 8 }}>{coachPaused ? 'Resume Coach' : 'Pause Coach'}</div>
            <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 24 }}>{coachPaused ? 'Your coach will resume sending check-ins.' : 'Your coach will stop texting until you unpause.'}</div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => setShowPauseModal(false)} style={{ flex: 1, background: 'var(--bg-dark)', border: '1px solid var(--border)', borderRadius: 9, padding: '10px', fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)', cursor: 'pointer' }}>Keep Coaching</button>
              <button onClick={handlePauseToggle} style={{ flex: 1, background: coachPaused ? 'var(--success-green)' : 'var(--warning-red)', border: 'none', borderRadius: 9, padding: '10px', fontSize: 14, fontWeight: 600, color: '#FFFFFF', cursor: 'pointer' }}>
                {coachPaused ? '▶️ Resume' : '⏸️ Pause'}
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );

  /* ══════════════════════════════════════════
     MOBILE LAYOUT
  ══════════════════════════════════════════ */
  if (isMobile) {
    return (
      <div style={{ minHeight: '100vh', background: 'var(--bg-dark)', display: 'flex', flexDirection: 'column', fontFamily: 'Inter, sans-serif' }}>
        <style>{`@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');`}</style>

        {/* Header */}
        <div style={{ background: 'var(--bg-card)', borderBottom: '1px solid var(--border)', padding: '14px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', position: 'sticky', top: 0, zIndex: 50 }}>
          <span style={{ fontFamily: 'Inter, sans-serif', fontSize: 22, color: 'var(--accent-blue)', letterSpacing: '-0.5px' }}>stackd</span>
          <div style={{ width: 36, height: 36, borderRadius: '50%', background: 'var(--bg-card)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700, color: 'var(--accent-blue)', border: '2px solid var(--accent-blue)' }}>{userInitials}</div>
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 80 }}>

          {activeTab === 'overview' && (
            <div style={{ padding: '24px 18px', display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div>
                <h1 style={{ fontFamily: 'Inter, sans-serif', fontSize: 26, color: 'var(--text-primary)', lineHeight: 1.2, marginBottom: 6 }}>{getGreeting()}, {userName} 👋</h1>
                <p style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Let's keep your streak alive.</p>
              </div>

              {/* Mini stat row */}
              <div style={{ display: 'flex', gap: 8 }}>
                {[
                  { v: `${currentStreak}`, l: 'streak', icon: '🔥' },
                  { v: `${goals.length}`, l: 'goals' },
                  { v: `${progressPct}%`, l: 'this month' },
                ].map(s => (
                  <div key={s.l} style={{ flex: 1, background: 'var(--bg-card)', borderRadius: 12, padding: '12px', border: '1px solid var(--border)', textAlign: 'center' }}>
                    <div style={{ fontFamily: 'Inter, sans-serif', fontSize: 22, color: 'var(--text-primary)' }}>{s.v}{s.icon}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>{s.l}</div>
                  </div>
                ))}
              </div>

              {/* Goals */}
              <div>
                <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>Today's Goals</h2>
                {goals.length === 0 ? (
                  <div style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
                    No goals yet — complete the quiz to add goals
                  </div>
                ) : (
                  goals.map(goal => (
                    <div key={goal.id} style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '16px', marginBottom: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                        <div style={{ width: 8, height: 8, borderRadius: '50%', background: goalColor(goal.category), flexShrink: 0 }} />
                        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{goal.activity}</span>
                        <span style={{ marginLeft: 'auto', fontSize: 11, background: 'rgba(77, 163, 255, 0.15)', color: 'var(--accent-blue)', padding: '2px 8px', borderRadius: 20, fontWeight: 600 }}>{goal.category}</span>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{goal.days.join(', ')}</div>
                    </div>
                  ))
                )}
              </div>

              {/* Milestones */}
              <div>
                <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>Milestones</h2>
                <div style={{ display: 'flex', gap: 8, overflowX: 'auto', paddingBottom: 4 }}>
                  {BADGE_DEFS.map(b => {
                    const earned = bestStreak >= b.streakNeeded;
                    return (
                      <div key={b.label} style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '12px 10px', borderRadius: 12, border: earned ? '1px solid var(--accent-blue)' : '1px solid var(--border)', background: earned ? 'rgba(77, 163, 255, 0.15)' : 'var(--bg-card)', opacity: earned ? 1 : 0.5, cursor: 'pointer' }}>
                        <span style={{ fontSize: 22 }}>{b.emoji}</span>
                        <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-primary)', textAlign: 'center', whiteSpace: 'nowrap' }}>{earned ? b.label : `+${b.streakNeeded - bestStreak}d`}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'goals' && (
            <div style={{ padding: '24px 18px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <h1 style={{ fontFamily: 'Inter, sans-serif', fontSize: 26, color: 'var(--text-primary)' }}>Your Goals</h1>
                <button style={{ background: 'var(--accent-blue)', color: 'var(--bg-dark)', border: 'none', borderRadius: 8, padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>+ Add</button>
              </div>
              {goals.length === 0 ? (
                <div style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '32px 24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
                  No goals yet — complete the quiz to add goals
                </div>
              ) : (
                goals.map(goal => (
                  <div key={goal.id} style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '18px', marginBottom: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                      <div style={{ width: 36, height: 36, borderRadius: 10, background: 'rgba(77, 163, 255, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <div style={{ width: 12, height: 12, borderRadius: '50%', background: goalColor(goal.category) }} />
                      </div>
                      <div>
                        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{goal.activity}</div>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{goalSub(goal.category)}</div>
                      </div>
                    </div>
                    <div style={{ height: 5, background: 'var(--border)', borderRadius: 3 }}>
                      <div style={{ height: '100%', width: `${Math.round(goal.streak / 7 * 100)}%`, background: goalColor(goal.category), borderRadius: 3 }} />
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>{goal.streak} / 7 this week</div>
                  </div>
                ))
              )}
            </div>
          )}

          {activeTab === 'coach' && (
            <div style={{ padding: '24px 18px' }}>
              <h1 style={{ fontFamily: 'Inter, sans-serif', fontSize: 26, color: 'var(--text-primary)', marginBottom: 20 }}>Your Coach</h1>
              <div style={{ background: 'var(--bg-card)', borderRadius: 16, padding: '24px', color: 'var(--text-primary)', marginBottom: 16, border: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
                  <div style={{ width: 60, height: 60, borderRadius: '50%', background: 'var(--accent-blue-12)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, fontWeight: 700, color: 'var(--accent-blue)', border: '2px solid var(--accent-blue)', flexShrink: 0 }}>{coachName.charAt(0).toUpperCase()}</div>
                  <div>
                    <div style={{ fontSize: 18, fontWeight: 700 }}>{coachName}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>Your AI accountability partner</div>
                    <div style={{ display: 'flex', gap: 5, marginTop: 6 }}>
                      {['Motivating','Direct','Empathetic'].map(t => (
                        <span key={t} style={{ fontSize: 10, background: 'rgba(77, 163, 255, 0.2)', color: 'var(--accent-blue)', padding: '2px 8px', borderRadius: 20, fontWeight: 500 }}>{t}</span>
                      ))}
                    </div>
                  </div>
                </div>
                <div style={{ background: 'rgba(77, 163, 255, 0.1)', borderRadius: 10, padding: '12px 14px', fontSize: 13, color: 'var(--text-secondary)', fontStyle: 'italic', fontFamily: 'Inter, sans-serif' }}>"Discipline today, freedom tomorrow."</div>
              </div>
              <button onClick={handleSave} style={{ background: saved ? 'var(--success-green)' : 'var(--accent-blue)', color: saved ? 'var(--bg-dark)' : 'var(--bg-dark)', border: 'none', borderRadius: 10, padding: '13px', width: '100%', fontSize: 14, fontWeight: 600, cursor: 'pointer', transition: 'background 0.3s' }}>{saved ? '✓ Saved!' : 'Save Changes'}</button>
            </div>
          )}

          {activeTab === 'settings' && (
            <div style={{ padding: '24px 18px' }}>
              <h1 style={{ fontFamily: 'Inter, sans-serif', fontSize: 26, color: 'var(--text-primary)', marginBottom: 20 }}>Settings</h1>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {[
                  { label: 'Morning check-in', sub: 'Daily text from your coach at 9 AM', key: 'morning' as const },
                  { label: 'Evening recap',     sub: 'Daily progress summary at 8 PM',     key: 'evening' as const },
                  { label: 'Weekly report',     sub: 'Summary of your week every Sunday',  key: 'weekly' as const },
                ].map(n => (
                  <div key={n.key} style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{n.label}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{n.sub}</div>
                    </div>
                    <Toggle on={notifs[n.key]} onChange={v => setNotifs(x => ({ ...x, [n.key]: v }))} label={n.label} />
                  </div>
                ))}
                <div style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Daily Check-in</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{checkinTime}</div>
                  </div>
                  <button onClick={() => setShowTimeModal(true)} style={{ background: 'rgba(77, 163, 255, 0.15)', color: 'var(--accent-blue)', border: '1px solid var(--accent-blue-12)', borderRadius: 8, padding: '6px 12px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>Edit</button>
                </div>
                <div style={{ background: 'var(--bg-card)', borderRadius: 14, border: '1px solid var(--border)', padding: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--warning-red)' }}>Pause coaching</div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>Temporarily stop check-ins</div>
                  </div>
                  <button onClick={() => setShowPauseModal(true)} style={{ background: coachPaused ? 'rgba(61, 220, 151, 0.15)' : 'rgba(255, 92, 92, 0.15)', color: coachPaused ? 'var(--success-green)' : 'var(--warning-red)', border: coachPaused ? '1px solid var(--success-green-12)' : '1px solid var(--warning-red-12)', borderRadius: 8, padding: '6px 12px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>{coachPaused ? 'Resume' : 'Pause'}</button>
                </div>
                <button style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', fontSize: 14, fontWeight: 500, cursor: 'pointer', padding: '10px', textAlign: 'center' }}>Sign out</button>
              </div>
            </div>
          )}
        </div>

        {/* Bottom Nav */}
        <nav style={{ position: 'fixed', bottom: 0, left: 0, right: 0, background: 'var(--bg-card)', borderTop: '1px solid var(--border)', display: 'flex', padding: '8px 8px 16px', gap: 4, zIndex: 50 }}>
          {[
            { id: 'overview', icon: '⊞', label: 'Today'    },
            { id: 'goals',    icon: '◎', label: 'Goals'    },
            { id: 'coach',    icon: '✦', label: 'Coach'    },
            { id: 'settings', icon: '⚙', label: 'Settings' },
          ].map(item => (
            <button key={item.id} onClick={() => setActiveTab(item.id as any)}
              aria-current={activeTab === item.id ? 'page' : undefined}
              style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, padding: '8px 4px', borderRadius: 12, background: activeTab === item.id ? 'rgba(77, 163, 255, 0.15)' : 'transparent', border: 'none', cursor: 'pointer', color: activeTab === item.id ? 'var(--accent-blue)' : 'var(--text-secondary)', transition: 'all 0.15s' }}>
              <span style={{ fontSize: 18 }} aria-hidden="true">{item.icon}</span>
              <span style={{ fontSize: 11, fontWeight: activeTab === item.id ? 600 : 400 }}>{item.label}</span>
            </button>
          ))}
        </nav>
        {modals}
      </div>
    );
  }

  /* ══════════════════════════════════════════
     DESKTOP LAYOUT
  ══════════════════════════════════════════ */
  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--bg-dark)', overflow: 'hidden', fontFamily: 'Inter, sans-serif', color: 'var(--text-primary)' }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');`}</style>

      {/* ── SIDEBAR ── */}
      <div style={{ width: 220, background: 'var(--bg-card)', display: 'flex', flexDirection: 'column', flexShrink: 0, height: '100vh', position: 'sticky', top: 0, borderRight: '1px solid var(--border)' }}>
        {/* Logo */}
        <div style={{ padding: '28px 24px 24px', borderBottom: '1px solid var(--border)' }}>
          <span style={{ fontFamily: 'Inter, sans-serif', fontSize: 24, color: 'var(--accent-blue)', letterSpacing: '-0.5px' }}>stackd</span>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>SMS accountability</div>
        </div>

        {/* Nav */}
        <div style={{ flex: 1, padding: '16px 12px' }}>
          {[
            { id: 'overview',  icon: '⊞', label: 'Overview'  },
            { id: 'goals',     icon: '◎', label: 'Goals'     },
            { id: 'coach',     icon: '✦', label: 'Coach'     },
            { id: 'settings',  icon: '⚙', label: 'Settings'  },
          ].map(item => (
            <button key={item.id} onClick={() => setActiveTab(item.id as any)}
              aria-current={activeTab === item.id ? 'page' : undefined}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 12px', borderRadius: 10, marginBottom: 2, cursor: 'pointer', background: activeTab === item.id ? 'rgba(77, 163, 255, 0.15)' : 'transparent', color: activeTab === item.id ? 'var(--accent-blue)' : 'var(--text-secondary)', transition: 'all 0.15s', border: 'none', width: '100%', textAlign: 'left', fontFamily: 'Inter, sans-serif' }}
              onMouseEnter={e => { if (activeTab !== item.id) (e.currentTarget as HTMLElement).style.background = 'rgba(77, 163, 255, 0.08)'; }}
              onMouseLeave={e => { if (activeTab !== item.id) (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
            >
              <span style={{ fontSize: 15, width: 20, textAlign: 'center' }} aria-hidden="true">{item.icon}</span>
              <span style={{ fontSize: 14, fontWeight: activeTab === item.id ? 600 : 400 }}>{item.label}</span>
            </button>
          ))}
        </div>

        {/* Profile */}
        <div style={{ padding: '16px 12px', borderTop: '1px solid var(--border)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 8, padding: '0 2px', opacity: 0.7 }}>Your coach is always in your corner.</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 10, background: 'rgba(77, 163, 255, 0.1)', borderRadius: 10 }}>
            <div style={{ width: 32, height: 32, borderRadius: '50%', background: 'var(--accent-blue)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700, color: 'var(--bg-dark)', flexShrink: 0 }}>{userInitials}</div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{userName}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{userPhone}</div>
            </div>
          </div>
        </div>
      </div>

      {/* ── MAIN CONTENT ── */}

      {/* OVERVIEW */}
      {activeTab === 'overview' && (
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
          {/* Left scroll */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '32px 28px', background: 'var(--bg-dark)' }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 28 }}>
              <div>
                <h1 style={{ fontSize: 28, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.7px', marginBottom: 4 }}>Hey {userName.split(' ')[0]} 👋</h1>
                <p style={{ fontSize: 15, color: 'var(--text-secondary)' }}>Let's keep your streak alive.</p>
              </div>
              <button onClick={() => setActiveTab('coach')} style={{ background: 'var(--accent-blue)', color: 'var(--bg-dark)', border: 'none', borderRadius: 8, padding: '10px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>✦</span> Coach
              </button>
            </div>

            {/* 4 Stat Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 28 }}>
              <StatCard label="Current Streak"    value={String(currentStreak)} unit="days" icon="🔥" sub={`Best: ${bestStreak} days`} />
              <StatCard label="Goals On Track"     value={`${goalsOnTrack}/${goals.length}`} sub="this week" progress={progressPct} />
              <StatCard label="Messages This Week" value={String(messages.length)} icon="💬" sub="from your coach" />
              <StatCard label="Progress Score"     value={String(progressPct)} unit="/100" sub="Keep going." />
            </div>

            {/* Goals preview */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', marginBottom: 24, overflow: 'hidden' }}>
              <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.3px' }}>Your Goals</h2>
                <button onClick={() => setActiveTab('goals')} style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue)', cursor: 'pointer', background: 'none', border: 'none', fontFamily: 'Inter, sans-serif' }}>View all →</button>
              </div>
              {goals.length === 0 ? (
                <div style={{ padding: '32px 24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
                  No goals yet — complete the quiz to add goals
                </div>
              ) : (
                goals.map((g, i) => (
                  <div key={g.id}
                    style={{ padding: '14px 24px', borderBottom: i < goals.length - 1 ? '1px solid var(--border)' : 'none', display: 'flex', alignItems: 'center', gap: 16, transition: 'background 0.15s' }}
                    onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = 'rgba(77, 163, 255, 0.05)'}
                    onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = 'transparent'}
                  >
                    <div style={{ width: 9, height: 9, borderRadius: '50%', background: goalColor(g.category), flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{g.activity}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{goalSub(g.category)}</div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>{g.streak}<span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}>/7</span> <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>this week</span></span>
                      <div style={{ width: 72, height: 4, background: 'var(--border)', borderRadius: 2 }}>
                        <div style={{ height: '100%', width: `${Math.round(g.streak / 7 * 100)}%`, background: goalColor(g.category), borderRadius: 2 }} />
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>

            {/* Recent messages */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', overflow: 'hidden' }}>
              <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between' }}>
                <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>Recent Messages</h2>
                <button onClick={() => setActiveTab('coach')} style={{ fontSize: 13, fontWeight: 600, color: 'var(--accent-blue)', cursor: 'pointer', background: 'none', border: 'none', fontFamily: 'Inter, sans-serif' }}>View all →</button>
              </div>
              <div aria-live="polite" aria-label="Recent messages" style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
                {messages.length === 0 ? (
                  <div style={{ textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14, padding: '16px 0' }}>
                    Your first check-in will arrive soon
                  </div>
                ) : (
                  messages.slice(0, 3).reverse().map(m => (
                    <div key={m.id} style={{ display: 'flex', justifyContent: m.from === 'user' ? 'flex-end' : 'flex-start', gap: 10 }}>
                      {m.from === 'coach' && (
                        <div style={{ width: 34, height: 34, borderRadius: '50%', background: 'rgba(77, 163, 255, 0.15)', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700, color: 'var(--accent-blue)' }}>{coachName.charAt(0).toUpperCase()}</div>
                      )}
                      <div style={{ maxWidth: '72%' }}>
                        {m.from === 'coach' && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>Coach · {m.time}</div>}
                        <div style={{ background: m.from === 'user' ? 'var(--accent-blue)' : 'var(--border)', color: m.from === 'user' ? 'var(--bg-dark)' : 'var(--text-primary)', borderRadius: m.from === 'user' ? '14px 14px 4px 14px' : '14px 14px 14px 4px', padding: '10px 14px', fontSize: 13, lineHeight: 1.5, border: m.from === 'coach' ? '1px solid var(--border)' : 'none' }}>{m.body}</div>
                        {m.from === 'user' && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, textAlign: 'right' }}>{m.time}</div>}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Right panel */}
          <div style={{ width: 276, flexShrink: 0, overflowY: 'auto', padding: '32px 20px 32px 0', display: 'flex', flexDirection: 'column', gap: 18 }}>
            {/* Coach card */}
            <div style={{ background: 'var(--forest-dark)', borderRadius: 16, padding: '22px', color: '#FFFFFF' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.5px', textTransform: 'uppercase', marginBottom: 14 }}>Your Coach</div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', marginBottom: 14 }}>
                <div style={{ width: 60, height: 60, borderRadius: '50%', background: 'var(--forest-mid)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, fontWeight: 700, color: 'var(--forest-light)', marginBottom: 10, border: '2px solid var(--forest-light)' }}>{coachName.charAt(0).toUpperCase()}</div>
                <div style={{ fontSize: 15, fontWeight: 700 }}>{coachName}</div>
                <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', marginTop: 3 }}>Motivating · Direct · Knows when to push</div>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.07)', borderRadius: 10, padding: '11px 13px', fontSize: 12, color: 'rgba(255,255,255,0.65)', lineHeight: 1.55, fontStyle: 'italic', fontFamily: 'Syne, sans-serif' }}>"Discipline today, freedom tomorrow."</div>
            </div>

            {/* Focus card */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, padding: '20px', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.5px', textTransform: 'uppercase', marginBottom: 14 }}>This Week's Focus</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.4, marginBottom: 6 }}>Consistency over perfection.</div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>Show up, even on the hard days.</div>
              <div style={{ marginTop: 14, textAlign: 'center' }}>
                <div style={{ width: 44, height: 44, borderRadius: '50%', border: '3px solid var(--forest-accent)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>🎯</div>
              </div>
            </div>

            {/* Insights card */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, padding: '20px', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.5px', textTransform: 'uppercase', marginBottom: 14 }}>Insights</div>
              {[
                { icon: '📅', text: "You're most consistent on Mondays." },
                { icon: '😊', text: 'Your replies are 87% positive.' },
                { icon: '⚡', text: 'You crush it when you plan ahead.' },
              ].map((ins, i) => (
                <div key={i} style={{ display: 'flex', gap: 10, marginBottom: i < 2 ? 12 : 0, alignItems: 'flex-start' }}>
                  <div style={{ width: 30, height: 30, borderRadius: 8, background: 'var(--forest-xpale)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flexShrink: 0 }}>{ins.icon}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-mid)', lineHeight: 1.5, paddingTop: 6 }}>{ins.text}</div>
                </div>
              ))}
            </div>

            {/* Streak calendar */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, padding: '20px', border: '1px solid var(--border)' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', letterSpacing: '0.5px', textTransform: 'uppercase', marginBottom: 14 }}>Streak Calendar</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 4, marginBottom: 10 }}>
                {['S','M','T','W','T','F','S'].map((d, i) => <div key={i} style={{ textAlign: 'center', fontSize: 9, fontWeight: 600, color: 'var(--text-secondary)' }}>{d}</div>)}
                {Array.from({ length: 28 }).map((_, i) => (
                  <div key={i} style={{ width: '100%', aspectRatio: '1', borderRadius: 5, background: i < currentStreak ? (i === currentStreak - 1 ? 'var(--forest-dark)' : 'var(--forest-accent)') : 'var(--off-white)', border: i === currentStreak - 1 ? '2px solid var(--forest-light)' : '1px solid var(--border)' }} />
                ))}
              </div>
              <div style={{ textAlign: 'center', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{currentStreak} day streak 🔥</div>
            </div>
          </div>
        </div>
      )}

      {/* GOALS */}
      {activeTab === 'goals' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '32px 36px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28 }}>
            <div>
              <h1 style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.6px', marginBottom: 4 }}>Your Goals</h1>
              <p style={{ fontSize: 14, color: 'var(--text-secondary)' }}>{goals.length} active goals · {goals.filter(g => g.streak === 7).length} completed this week</p>
            </div>
            <button style={{ background: 'var(--forest-dark)', color: '#FFFFFF', border: 'none', borderRadius: 9, padding: '10px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>+ Add Goal</button>
          </div>

          {/* Filter tabs */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            {['All','Health','Learning','Routine','Finance'].map(c => (
              <button key={c} onClick={() => setFilter(c)} style={{ background: filter === c ? 'var(--accent-blue)' : 'var(--bg-card)', color: filter === c ? 'var(--bg-dark)' : 'var(--text-secondary)', border: filter === c ? '1px solid var(--accent-blue)' : '1px solid var(--border)', borderRadius: 20, padding: '6px 16px', fontSize: 13, fontWeight: filter === c ? 600 : 400, cursor: 'pointer', transition: 'all 0.15s' }}>{c}</button>
            ))}
          </div>

          {goals.length === 0 ? (
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', padding: '48px 24px', textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
              No goals yet — complete the quiz to add goals
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {goals.map(g => {
                const pct = Math.round(g.streak / 7 * 100);
                const col = goalColor(g.category);
                return (
                  <div key={g.id} style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', padding: '22px 24px', transition: 'box-shadow 0.2s' }}
                    onMouseEnter={e => (e.currentTarget as HTMLElement).style.boxShadow = '0 4px 20px rgba(27,67,50,0.08)'}
                    onMouseLeave={e => (e.currentTarget as HTMLElement).style.boxShadow = 'none'}
                  >
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 14 }}>
                      <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
                        <div style={{ width: 42, height: 42, borderRadius: 12, background: col + '18', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <div style={{ width: 14, height: 14, borderRadius: '50%', background: col }} />
                        </div>
                        <div>
                          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.3px' }}>{g.activity}</div>
                          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{goalSub(g.category)}</div>
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <span style={{ background: 'var(--forest-xpale)', color: 'var(--forest-accent)', fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 20 }}>{g.category}</span>
                        <span style={{ background: 'var(--off-white)', color: 'var(--text-secondary)', fontSize: 11, padding: '3px 10px', borderRadius: 20, border: '1px solid var(--border)' }}>{g.days.includes('Daily') ? 'Daily' : 'Weekly'}</span>
                      </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                      <div style={{ flex: 1, height: 6, background: 'var(--border)', borderRadius: 3 }}>
                        <div style={{ height: '100%', width: `${pct}%`, background: col, borderRadius: 3, transition: 'width 0.5s' }} />
                      </div>
                      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', whiteSpace: 'nowrap' }}>{g.streak} / 7</span>
                      <span style={{ fontSize: 12, color: pct === 100 ? 'var(--forest-accent)' : 'var(--text-secondary)', fontWeight: pct === 100 ? 700 : 400 }}>{pct}%</span>
                    </div>
                    <div style={{ display: 'flex', gap: 5, marginTop: 14 }}>
                      {Array.from({ length: 7 }).map((_, i) => (
                        <div key={i} style={{ width: 28, height: 28, borderRadius: 8, background: i < g.streak ? col : 'var(--off-white)', border: `1.5px solid ${i < g.streak ? col : 'var(--border)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: i < g.streak ? '#FFFFFF' : 'var(--text-secondary)', fontWeight: 600 }}>
                          {i < g.streak ? '✓' : ''}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* COACH */}
      {activeTab === 'coach' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '32px 36px' }}>
          <div style={{ marginBottom: 28 }}>
            <h1 style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.6px', marginBottom: 4 }}>Your Coach</h1>
            <p style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Customize how your AI accountability coach works for you.</p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
            {/* Coach profile card */}
            <div style={{ background: 'var(--forest-dark)', borderRadius: 18, padding: '28px', color: '#FFFFFF', display: 'flex', flexDirection: 'column', gap: 20 }}>
              <div style={{ display: 'flex', gap: 18, alignItems: 'center' }}>
                <div style={{ width: 72, height: 72, borderRadius: '50%', background: 'var(--forest-mid)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 28, fontWeight: 700, color: 'var(--forest-light)', border: '2px solid var(--forest-light)', flexShrink: 0 }}>{coachName.charAt(0).toUpperCase()}</div>
                <div>
                  <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.4px' }}>{coachName}</div>
                  <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', marginTop: 4 }}>Your AI accountability partner</div>
                  <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
                    {['Motivating','Direct','Empathetic'].map(t => (
                      <span key={t} style={{ fontSize: 11, background: 'rgba(116,198,157,0.2)', color: 'var(--forest-light)', padding: '3px 9px', borderRadius: 20, fontWeight: 500 }}>{t}</span>
                    ))}
                  </div>
                </div>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.07)', borderRadius: 12, padding: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,0.4)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 8 }}>Coach's Motto</div>
                <div style={{ fontFamily: 'Syne, sans-serif', fontSize: 18, color: '#FFFFFF', fontStyle: 'italic', lineHeight: 1.4 }}>"Discipline today, freedom tomorrow."</div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                {[{ v: '47', l: 'Days Together' }, { v: '94%', l: 'Response Rate' }, { v: '4.9★', l: 'Your Rating' }].map(s => (
                  <div key={s.l} style={{ textAlign: 'center' }}>
                    <div style={{ fontFamily: 'Syne, sans-serif', fontSize: 22, color: 'var(--forest-light)' }}>{s.v}</div>
                    <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.4)', marginTop: 2 }}>{s.l}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Customize panel */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', padding: '22px' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Coaching Style</div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 14 }}>How you want your coach to communicate.</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }}>
                  {['Motivating','Gentle','Direct','Analytical'].map(s => (
                    <button key={s} onClick={() => setCoachStyle(s)} style={{ padding: 10, borderRadius: 10, border: coachStyle === s ? '2px solid var(--forest-accent)' : '2px solid var(--border)', background: coachStyle === s ? 'var(--forest-xpale)' : 'var(--off-white)', fontSize: 13, fontWeight: coachStyle === s ? 600 : 400, color: coachStyle === s ? 'var(--forest-accent)' : 'var(--text-mid)', cursor: 'pointer', transition: 'all 0.15s', textAlign: 'center' }}>{s}</button>
                  ))}
                </div>
              </div>
              <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', padding: '22px' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Check-in Time</div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 14 }}>When your coach texts you each day.</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }}>
                  {['Morning','Midday','Evening','Multiple'].map(t => (
                    <button key={t} onClick={() => setCheckInPref(t)} style={{ padding: 10, borderRadius: 10, border: checkInPref === t ? '2px solid var(--forest-accent)' : '2px solid var(--border)', background: checkInPref === t ? 'var(--forest-xpale)' : 'var(--off-white)', fontSize: 13, fontWeight: checkInPref === t ? 600 : 400, color: checkInPref === t ? 'var(--forest-accent)' : 'var(--text-mid)', cursor: 'pointer', transition: 'all 0.15s', textAlign: 'center' }}>{t}</button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <button onClick={handleSave} style={{ background: saved ? 'var(--forest-accent)' : 'var(--forest-dark)', color: '#FFFFFF', border: 'none', borderRadius: 10, padding: '13px 28px', fontSize: 14, fontWeight: 600, cursor: 'pointer', transition: 'background 0.3s' }}>
            {saved ? '✓ Saved!' : 'Save Changes'}
          </button>
        </div>
      )}

      {/* SETTINGS */}
      {activeTab === 'settings' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: '32px 36px' }}>
          <div style={{ marginBottom: 28 }}>
            <h1 style={{ fontSize: 26, fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.6px', marginBottom: 4 }}>Settings</h1>
            <p style={{ fontSize: 14, color: 'var(--text-secondary)' }}>Manage your account and preferences.</p>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 20, maxWidth: 680 }}>
            {/* Profile */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', overflow: 'hidden' }}>
              <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>Profile</div>
              </div>
              <div style={{ padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                  <div style={{ width: 60, height: 60, borderRadius: '50%', background: 'var(--forest-mid)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, fontWeight: 700, color: 'var(--forest-light)', flexShrink: 0 }}>{userInitials}</div>
                  <div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>{userName}</div>
                    <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Member since Jan 2024</div>
                    <span style={{ fontSize: 11, background: 'var(--forest-xpale)', color: 'var(--forest-accent)', padding: '3px 10px', borderRadius: 20, fontWeight: 600, marginTop: 6, display: 'inline-block' }}>Pro Plan</span>
                  </div>
                </div>
                {[
                  { label: 'Full Name',    value: userName,   setter: setUserName,  type: 'text' },
                  { label: 'Phone Number', value: userPhone,  setter: setUserPhone, type: 'tel'  },
                ].map(field => (
                  <div key={field.label}>
                    <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>{field.label}</label>
                    <input value={field.value} onChange={e => field.setter(e.target.value)} type={field.type}
                      style={{ width: '100%', border: '1.5px solid var(--border)', borderRadius: 9, padding: '10px 14px', fontSize: 14, color: 'var(--text-primary)', outline: 'none', fontFamily: 'DM Sans, sans-serif', transition: 'border-color 0.15s' }}
                      onFocus={e => (e.target as HTMLInputElement).style.borderColor = 'var(--forest-accent)'}
                      onBlur={e => (e.target as HTMLInputElement).style.borderColor = 'var(--border)'}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Notifications */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', overflow: 'hidden' }}>
              <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>Notifications</div>
              </div>
              <div style={{ padding: '8px 0' }}>
                {[
                  { key: 'morning', label: 'Morning check-in', sub: 'Daily text from your coach at 9 AM' },
                  { key: 'evening', label: 'Evening recap',     sub: 'Daily progress summary at 8 PM'    },
                  { key: 'weekly',  label: 'Weekly report',     sub: 'Summary of your week every Sunday' },
                ].map((n, i) => (
                  <div key={n.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 24px', borderBottom: i < 2 ? '1px solid var(--border)' : 'none' }}>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{n.label}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{n.sub}</div>
                    </div>
                    <Toggle on={notifs[n.key as keyof typeof notifs]} onChange={v => setNotifs(x => ({ ...x, [n.key]: v }))} label={n.label} />
                  </div>
                ))}
              </div>
            </div>

            {/* Plan */}
            <div style={{ background: 'var(--forest-dark)', borderRadius: 16, padding: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.5px', textTransform: 'uppercase', marginBottom: 6 }}>Current Plan</div>
                <div style={{ fontFamily: 'Syne, sans-serif', fontSize: 22, color: '#FFFFFF' }}>Pro · $12/mo</div>
                <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.5)', marginTop: 4 }}>Renews May 26, 2026</div>
              </div>
              <button style={{ background: 'var(--forest-light)', color: 'var(--forest-dark)', border: 'none', borderRadius: 9, padding: '10px 20px', fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>Manage Plan</button>
            </div>

            {/* Account */}
            <div style={{ background: 'var(--bg-card)', borderRadius: 16, border: '1px solid var(--border)', overflow: 'hidden' }}>
              <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>Account</div>
              </div>
              <div style={{ padding: '8px 0' }}>
                {[
                  { label: 'Export my data',  sub: 'Download all your goals and messages',     color: 'var(--text-primary)', action: () => {} },
                  { label: 'Pause coaching',   sub: 'Temporarily stop check-ins',               color: '#F97316',  action: () => setShowPauseModal(true) },
                  { label: 'Delete account',   sub: 'Permanently delete your account and data', color: '#EF4444',  action: () => {} },
                ].map((item, i) => (
                  <button key={item.label} onClick={item.action}
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 24px', borderBottom: i < 2 ? '1px solid var(--border)' : 'none', cursor: 'pointer', transition: 'background 0.15s', width: '100%', background: 'transparent', border: 'none', textAlign: 'left', fontFamily: 'Inter, sans-serif' }}
                    onMouseEnter={e => (e.currentTarget as HTMLElement).style.background = 'var(--off-white)'}
                    onMouseLeave={e => (e.currentTarget as HTMLElement).style.background = 'transparent'}
                  >
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: item.color }}>{item.label}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{item.sub}</div>
                    </div>
                    <span style={{ color: 'var(--text-secondary)' }} aria-hidden="true">→</span>
                  </button>
                ))}
              </div>
            </div>

            <button onClick={handleSave} style={{ background: saved ? 'var(--forest-accent)' : 'var(--forest-dark)', color: '#FFFFFF', border: 'none', borderRadius: 10, padding: '13px 28px', fontSize: 14, fontWeight: 600, cursor: 'pointer', transition: 'background 0.3s', alignSelf: 'flex-start' }}>
              {saved ? '✓ Saved!' : 'Save Changes'}
            </button>
          </div>
        </div>
      )}

      {modals}
    </div>
  );
}
