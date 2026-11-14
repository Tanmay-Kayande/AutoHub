"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/lib/auth";

const LINKS = [
  { href: "/brands", label: "Brands" },
  { href: "/compare", label: "Compare" },
  { href: "/ask", label: "Ask AutoHub" },
  { href: "/news", label: "News" },
];

export function Nav() {
  const pathname = usePathname();
  const { user, ready, logout } = useAuth();
  const [open, setOpen] = useState(false);

  const linkClass = (href: string) =>
    `rounded-full px-3 py-1.5 text-sm transition-colors ${
      pathname.startsWith(href) ? "bg-surface-2 text-text" : "text-muted hover:text-text"
    }`;

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-md">
      <nav className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6" aria-label="Main">
        <Link href="/" className="font-display text-lg font-semibold tracking-tight">
          Auto<span className="text-accent">Hub</span>
        </Link>
        <div className="hidden items-center gap-1 md:flex">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} className={linkClass(link.href)}>
              {link.label}
            </Link>
          ))}
        </div>
        <div className="hidden items-center gap-3 md:flex">
          {ready && user ? (
            <>
              <Link href="/profile" className="text-sm text-muted hover:text-text">
                {user.name}
              </Link>
              <button onClick={logout} className="text-sm text-muted hover:text-text">
                Log out
              </button>
            </>
          ) : ready ? (
            <>
              <Link href="/login" className="text-sm text-muted hover:text-text">
                Log in
              </Link>
              <Link href="/signup" className="rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-bg">
                Sign up
              </Link>
            </>
          ) : null}
        </div>
        <button
          className="rounded-md p-2 text-muted md:hidden"
          aria-expanded={open}
          aria-controls="mobile-menu"
          aria-label="Menu"
          onClick={() => setOpen((v) => !v)}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
          </svg>
        </button>
      </nav>
      {open ? (
        <div id="mobile-menu" className="border-t border-line px-4 pb-4 md:hidden">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} onClick={() => setOpen(false)} className="block py-3 text-text">
              {link.label}
            </Link>
          ))}
          <div className="mt-2 flex gap-4 border-t border-line pt-3 text-sm">
            {user ? (
              <>
                <Link href="/profile" onClick={() => setOpen(false)}>
                  {user.name}
                </Link>
                <button onClick={logout} className="text-muted">
                  Log out
                </button>
              </>
            ) : (
              <>
                <Link href="/login" onClick={() => setOpen(false)}>
                  Log in
                </Link>
                <Link href="/signup" onClick={() => setOpen(false)} className="text-accent">
                  Sign up
                </Link>
              </>
            )}
          </div>
        </div>
      ) : null}
    </header>
  );
}
