// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Barlow, Spline_Sans_Mono } from "next/font/google";
import { cookies } from "next/headers";
import { LocaleProvider } from "web-shared/i18n";
import { htmlLang, LOCALE_COOKIE, parseLocale } from "web-shared/locale";
import { ZH } from "../lib/i18n-zh";
import "./globals.css";

// Barlow for the brand and labels; Spline Sans Mono with tabular figures for numbers.
const barlow = Barlow({
  subsets: ["latin"],
  weight: ["100", "300", "400", "500", "600", "700", "800"],
  variable: "--font-body",
  display: "swap",
});

const splineSansMono = Spline_Sans_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ACME Mobile Merchant",
  description: "The ACME Mobile back office, an example for the merchant agent.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return (
    <html lang={htmlLang(locale)} suppressHydrationWarning className={`${barlow.variable} ${splineSansMono.variable}`}>
      <body suppressHydrationWarning>
        <LocaleProvider initial={locale} messages={ZH}>
          {children}
        </LocaleProvider>
      </body>
    </html>
  );
}
