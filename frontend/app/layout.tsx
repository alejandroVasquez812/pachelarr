import type { Metadata } from "next";
import Link from "next/link";
import { Toaster } from "@/components/Toaster";
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
    // Tremor Raw convention: dark mode via .dark class + bg-gray-950 for the dark background.
    <html lang="en" className="dark antialiased">
      <body className="min-h-screen bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-50">
        <div className="flex min-h-screen">
          <aside className="w-56 shrink-0 border-r border-gray-200 p-4 space-y-1 dark:border-gray-800">
            <div className="px-3 py-2 mb-2">
              <span className="text-lg font-semibold text-gray-900 dark:text-gray-50">
                Pachelarr
              </span>
            </div>
            <Link
              href="/dashboard"
              className="block px-3 py-2 rounded-md text-gray-700 hover:bg-gray-100 transition-colors dark:text-gray-300 dark:hover:bg-gray-800/80"
            >
              Dashboard
            </Link>
            <Link
              href="/settings"
              className="block px-3 py-2 rounded-md text-gray-700 hover:bg-gray-100 transition-colors dark:text-gray-300 dark:hover:bg-gray-800/80"
            >
              Settings
            </Link>
          </aside>
          <main className="flex-1 p-6 overflow-auto">{children}</main>
        </div>
        <Toaster />
      </body>
    </html>
  );
}