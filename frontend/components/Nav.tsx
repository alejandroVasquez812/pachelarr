"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { RiMenuLine, RiCloseLine } from "@remixicon/react";
import { ThemeToggle } from "./ThemeToggle";
import { cx } from "@/lib/utils";
import { SETTINGS_TABS, SETTINGS_GROUPS } from "@/lib/types";

function useHash(): string {
  const [hash, setHash] = useState("");
  useEffect(() => {
    const update = () => setHash(window.location.hash.slice(1));
    update();
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return hash;
}

const topLevelNavItems = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/settings", label: "Settings" },
];

interface NavLinkProps {
  href: string;
  label: string;
  sub?: boolean;
  active?: boolean;
  onClick?: () => void;
}

function NavLink({
  href,
  label,
  sub = false,
  active: activeProp,
  onClick,
}: NavLinkProps) {
  const pathname = usePathname();
  const active = activeProp ?? pathname === href;

  return (
    <Link
      href={href}
      onClick={onClick}
      className={cx(
        "block rounded-md text-sm transition-colors",
        sub
          ? "px-3 py-1.5 ml-2 border-l border-[var(--border)]"
          : "px-3 py-2",
        "text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--text)]",
        active && "bg-[var(--surface)] text-[var(--text)]"
      )}
    >
      {label}
    </Link>
  );
}

function Wordmark() {
  return (
    <span className="text-lg font-semibold text-[var(--text)] tracking-tight">
      Pachelarr
    </span>
  );
}

function SettingsSubNav({ onClick }: { onClick?: () => void }) {
  const pathname = usePathname();
  const onSettings = pathname === "/settings";
  const hash = useHash();

  return (
    <div className="space-y-1 mt-1">
      {SETTINGS_TABS.map((tab) => {
        const tabHash = `tab-${tab.id}`;
        const groupLinks = SETTINGS_GROUPS.filter((g) => g.tab === tab.id);
        return (
          <div key={tab.id}>
            <NavLink
              href={`/settings#${tabHash}`}
              label={tab.label}
              sub
              active={onSettings && hash === tabHash}
              onClick={onClick}
            />
            <div className="space-y-0.5 mt-0.5">
              {groupLinks.map((group) => (
                <NavLink
                  key={group.name}
                  href={`/settings#group-${group.name}`}
                  label={group.name}
                  sub
                  active={onSettings && hash === `group-${group.name}`}
                  onClick={onClick}
                />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Nav() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-60 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)]">
        <div className="px-4 py-5">
          <Wordmark />
        </div>
        <nav className="flex-1 px-3 space-y-1 overflow-y-auto">
          {topLevelNavItems.map((item) => (
            <div key={item.href}>
              <NavLink href={item.href} label={item.label} />
              {item.href === "/settings" && <SettingsSubNav />}
            </div>
          ))}
        </nav>
        <div className="px-3 py-4 border-t border-[var(--border)]">
          <ThemeToggle />
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="flex md:hidden fixed top-0 inset-x-0 z-40 h-14 items-center justify-between border-b border-[var(--border)] bg-[var(--surface)] px-4">
        <Wordmark />
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open navigation"
          aria-expanded={open}
          className="inline-flex items-center justify-center rounded-md p-2 text-[var(--text)] transition-colors hover:bg-[var(--surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring-focus)]"
        >
          <RiMenuLine className="size-6" aria-hidden="true" />
        </button>
      </header>

      {/* Mobile drawer overlay */}
      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/40 md:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Mobile drawer */}
      <div
        className={cx(
          "fixed inset-y-0 right-0 z-50 w-72 flex-col border-l border-[var(--border)] bg-[var(--surface)] md:hidden",
          "transition-transform duration-200 ease-out",
          open ? "translate-x-0" : "translate-x-full"
        )}
        aria-hidden={!open}
      >
        <div className="flex h-14 items-center justify-between px-4 border-b border-[var(--border)]">
          <Wordmark />
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close navigation"
            className="inline-flex items-center justify-center rounded-md p-2 text-[var(--text)] transition-colors hover:bg-[var(--surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring-focus)]"
          >
            <RiCloseLine className="size-6" aria-hidden="true" />
          </button>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {topLevelNavItems.map((item) => (
            <div key={item.href}>
              <NavLink
                href={item.href}
                label={item.label}
                onClick={() => setOpen(false)}
              />
              {item.href === "/settings" && (
                <SettingsSubNav onClick={() => setOpen(false)} />
              )}
            </div>
          ))}
        </nav>
        <div className="px-3 py-4 border-t border-[var(--border)]">
          <ThemeToggle />
        </div>
      </div>
    </>
  );
}

export { Nav };
