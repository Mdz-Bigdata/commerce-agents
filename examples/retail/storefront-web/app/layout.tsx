// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Instrument_Sans } from "next/font/google";
import { cookies } from "next/headers";
import { LocaleProvider } from "web-shared/i18n";
import { htmlLang, LOCALE_COOKIE, parseLocale } from "web-shared/locale";
import { ZH } from "../lib/i18n-zh";
import "./globals.css";

const instrumentSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ACME",
  description: "Shop the ACME catalog with the ACME Assistant.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const locale = parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  return (
    <html lang={htmlLang(locale)} suppressHydrationWarning className={instrumentSans.variable}>
      <body suppressHydrationWarning>
        <LocaleProvider initial={locale} messages={ZH}>
          {children}
        </LocaleProvider>
      </body>
    </html>
  );
}
