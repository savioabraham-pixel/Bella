import type { Metadata, Viewport } from "next";
import { Cormorant_Garamond, DM_Sans } from "next/font/google";

import "./globals.css";
import "./legacy.css";

/**
 * The two families the legacy design is built on, self-hosted at build time rather than
 * fetched from Google on every load. That keeps `font-src 'self'` achievable and removes
 * a third-party origin from the critical path of a page that renders someone's private
 * conversations.
 */
const display = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-display",
  display: "swap",
});

const body = DM_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Bella.ai", template: "%s · Bella" },
  description: "A personal AI companion.",
  applicationName: "Bella",
  // Nothing about the app is public, so keep it out of indexes entirely.
  robots: { index: false, follow: false },
  appleWebApp: { capable: true, title: "Bella", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FAF8F4" },
    { media: "(prefers-color-scheme: dark)", color: "#0B2545" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`legacy ${display.variable} ${body.variable}`}>
        <noscript>
          <p style={{ padding: "2rem", fontFamily: "system-ui" }}>
            Bella needs JavaScript to run. Please enable it and reload.
          </p>
        </noscript>
        {children}
      </body>
    </html>
  );
}
