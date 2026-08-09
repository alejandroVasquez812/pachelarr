import type { Metadata } from "next";
import Link from "next/link";
import { Toaster } from "react-hot-toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pachelarr Dashboard",
  description: "Monitoring and configuration dashboard for Pachelarr",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-tremor-background-muted dark:bg-dark-tremor-background-subtle text-tremor-content dark:text-dark-tremor-content">
        <div className="flex min-h-screen">
          <aside className="w-56 shrink-0 border-r border-tremor-border dark:border-dark-tremor-border p-4 space-y-1">
            <div className="px-3 py-2 mb-2">
              <span className="text-lg font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                Pachelarr
              </span>
            </div>
            <Link
              href="/dashboard"
              className="block px-3 py-2 rounded-tremor-default text-tremor-content-emphasis hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-muted transition-colors"
            >
              Dashboard
            </Link>
            <Link
              href="/settings"
              className="block px-3 py-2 rounded-tremor-default text-tremor-content hover:bg-tremor-background-subtle dark:hover:bg-dark-tremor-background-muted transition-colors"
            >
              Settings
            </Link>
          </aside>
          <main className="flex-1 p-6 overflow-auto">{children}</main>
        </div>
        <Toaster position="bottom-right" />
      </body>
    </html>
  );
}