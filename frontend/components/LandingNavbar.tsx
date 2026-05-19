'use client';

import { useEffect, useRef } from 'react';

const SMS_URL = `sms:${process.env.NEXT_PUBLIC_BLOOIO_NUMBER || '+15550000000'}&body=Hello`;

const SMS_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M16 3H4a2 2 0 00-2 2v10a2 2 0 002 2h9l4 4v-4h2a2 2 0 002-2V5a2 2 0 00-2-2z" />
  </svg>
);

export default function LandingNavbar() {
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
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
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <nav ref={navRef} className="lp-nav">
      <span className="lp-nav-logo">stackd</span>
      <a href={SMS_URL} className="lp-nav-pill">
        {SMS_ICON}
        Start now
      </a>
    </nav>
  );
}
