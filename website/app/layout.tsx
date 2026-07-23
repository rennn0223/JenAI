import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "JenAI Documentation",
    template: "%s · JenAI Documentation",
  },
  description:
    "Official documentation for the JenAI high-level robot decision agent and Isaac Sim reference workflow.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
