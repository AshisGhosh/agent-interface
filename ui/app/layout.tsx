import type { Metadata } from "next";
import "./globals.css";

import {
  THEME_INIT_SCRIPT,
  ThemeProvider,
} from "@/components/theme-provider";

export const metadata: Metadata = {
  title: "agi",
  description: "Agent Interface board",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="h-full">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
