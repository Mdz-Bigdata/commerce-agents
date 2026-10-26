// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Archivo, Big_Shoulders, IBM_Plex_Mono } from "next/font/google";
import { cookies } from "next/headers";
import { LocaleProvider } from "web-shared/i18n";
import { htmlLang, LOCALE_COOKIE, parseLocale } from "web-shared/locale";
import { ZH } from "../lib/i18n-zh";
import "./globals.css";

const bigShoulders = Big_Shoulders({
  subsets: ["latin"],
  weight: ["500", "600", "700", "800", "900"],
  variable: "--font-display",
  display: "swap",
  // next/font has no fallback metrics for this face yet.
  adjustFontFallback: false,
  fallback: ["Arial Narrow", "sans-serif"],
});

const archivo = Archivo({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-body",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ACME Tickets Merchant",
  description: "The ACME Tickets back office, an example for the merchant agent.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return (
    <html
      lang={htmlLang(locale)}
      suppressHydrationWarning
      className={`${bigShoulders.variable} ${archivo.variable} ${plexMono.variable}`}
    >
      <body suppressHydrationWarning>
        <LocaleProvider initial={locale} messages={ZH}>
          {children}
        </LocaleProvider>
      </body>
    </html>
  );
}
