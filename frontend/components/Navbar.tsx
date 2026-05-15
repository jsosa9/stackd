'use client';

import Link from "next/link";
import { useState, useEffect } from "react";
import { useAuthUser } from "@/lib/useAuthUser";
import { signInWithGoogle, signOut } from "@/lib/auth";

interface NavbarProps {
  step?: number;
  totalSteps?: number;
  showAuth?: boolean;
}

export default function Navbar({ step, totalSteps, showAuth = false }: NavbarProps) {
  const progress = step && totalSteps ? Math.round((step / totalSteps) * 100) : 0;
  const { user, loading } = useAuthUser();
  const [signingIn, setSigningIn] = useState(false);
  const [showMenu, setShowMenu] = useState(false);

  const handleSignIn = async () => {
    try {
      setSigningIn(true);
      await signInWithGoogle();
    } catch (error) {
      console.error('Sign in failed:', error);
      setSigningIn(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOut();
      setShowMenu(false);
    } catch (error) {
      console.error('Sign out failed:', error);
    }
  };

  useEffect(() => {
    if (!showMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowMenu(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showMenu]);

  return (
    <div style={{ position: "sticky", top: 0, zIndex: 50, background: "#121A23", borderBottom: "1px solid #1F2937" }}>
      <div className="max-w-150 mx-auto px-5 py-3 flex items-center justify-between">
        <Link href="/" style={{ textDecoration: "none" }}>
          <span
            style={{
              fontSize: 22,
              fontWeight: 700,
              color: "#4DA3FF",
              letterSpacing: "-0.5px",
              fontFamily: "Inter, sans-serif",
            }}
          >
            stackd
          </span>
        </Link>

        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {step && totalSteps && (
            <span
              style={{
                fontSize: 12,
                fontWeight: 700,
                color: "#9AA4AF",
                textTransform: "uppercase",
                letterSpacing: "1px",
                fontFamily: "Inter, sans-serif",
              }}
            >
              Step {step} of {totalSteps}
            </span>
          )}

          {showAuth && !loading && (
            <div style={{ position: "relative" }}>
              {!user ? (
                <button
                  onClick={handleSignIn}
                  disabled={signingIn}
                  style={{
                    padding: "8px 16px",
                    background: signingIn ? "#4DA3FF" : "#4DA3FF",
                    color: "#0B0F14",
                    border: "none",
                    borderRadius: "6px",
                    fontSize: 14,
                    fontWeight: 700,
                    cursor: signingIn ? "not-allowed" : "pointer",
                    opacity: signingIn ? 0.7 : 1,
                    fontFamily: "Inter, sans-serif",
                    transition: "all 0.3s ease",
                  }}
                >
                  {signingIn ? "Signing you in..." : "Sign in"}
                </button>
              ) : (
                <button
                  onClick={() => setShowMenu(!showMenu)}
                  aria-expanded={showMenu}
                  aria-haspopup="menu"
                  style={{
                    padding: "6px 12px",
                    background: "transparent",
                    border: "1px solid #4DA3FF",
                    borderRadius: "6px",
                    color: "#4DA3FF",
                    cursor: "pointer",
                    fontSize: 14,
                    fontWeight: 600,
                    fontFamily: "Inter, sans-serif",
                  }}
                >
                  {user.email?.split("@")[0] || "Account"}
                </button>
              )}

              {showMenu && user && (
                <div
                  role="menu"
                  style={{
                    position: "absolute",
                    top: "100%",
                    right: 0,
                    marginTop: "8px",
                    background: "#1F2937",
                    border: "1px solid #374151",
                    borderRadius: "8px",
                    overflow: "hidden",
                    minWidth: "150px",
                    boxShadow: "0 10px 25px rgba(0,0,0,0.3)",
                  }}
                >
                  <Link
                    href="/dashboard"
                    role="menuitem"
                    onClick={() => setShowMenu(false)}
                    style={{
                      display: "block",
                      padding: "12px 16px",
                      color: "#9AA4AF",
                      textDecoration: "none",
                      fontSize: 14,
                      borderBottom: "1px solid #374151",
                      transition: "background 0.2s",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#2D3748")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    Dashboard
                  </Link>
                  <button
                    onClick={handleSignOut}
                    role="menuitem"
                    style={{
                      width: "100%",
                      padding: "12px 16px",
                      background: "transparent",
                      border: "none",
                      color: "#EF4444",
                      fontSize: 14,
                      fontWeight: 600,
                      cursor: "pointer",
                      textAlign: "left",
                      fontFamily: "Inter, sans-serif",
                      transition: "background 0.2s",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "#2D3748")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    Sign out
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Progress bar */}
      {step && totalSteps && (
        <div
          role="progressbar"
          aria-valuenow={progress}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Quiz progress: step ${step} of ${totalSteps}`}
          style={{ height: 3, background: "#1F2937" }}
        >
          <div
            style={{
              height: "100%",
              background: "#4DA3FF",
              width: `${progress}%`,
              transition: "width 0.5s ease-out",
            }}
          />
        </div>
      )}
    </div>
  );
}
