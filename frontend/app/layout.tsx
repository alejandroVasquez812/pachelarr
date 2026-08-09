import type { Metadata } from "next";
import { Space_Grotesk, Sora } from "next/font/google";
import { ThemeProvider } from "@/components/ThemeProvider";
import { Nav } from "@/components/Nav";
import { Toaster } from "@/components/Toaster";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-heading",
});

const sora = Sora({
  subsets: ["latin"],
  variable: "--font-body",
});

export const metadata: Metadata = {
  title: "Pachelarr Dashboard",
  description: "Monitoring and configuration dashboard for Pachelarr",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const themeScript = `(function(){try{var s=localStorage.getItem('pachelarr-theme');var d=s==='dark'||(s!=='light'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

  return (
    <html lang="en" className="antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className={`${spaceGrotesk.variable} ${sora.variable} min-h-screen bg-[var(--bg)] text-[var(--text)]`}>
        <ThemeProvider>
          <div className="flex min-h-screen">
            <Nav />
            <main className="flex-1 overflow-auto md:p-6 pt-20 px-4 pb-6">{children}</main>
          </div>
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}