import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "agi",
  description: "Agent Interface board",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-full">{children}</body>
    </html>
  );
}
