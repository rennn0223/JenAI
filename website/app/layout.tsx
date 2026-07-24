import type { Metadata } from "next";

import "./globals.css";

const siteUrl = "https://jenai-docs.ren910223.chatgpt.site";
export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "JenAI Documentation",
    template: "%s · JenAI Documentation",
  },
  description:
    "Official documentation for the JenAI high-level robot decision agent and Isaac Sim reference workflow.",
  openGraph: {
    type: "website",
    url: siteUrl,
    siteName: "JenAI Documentation",
    title: "JenAI — High-level robot decision agent",
    description:
      "Robot workflows selected by AI, executed deterministically with evidence.",
    images: [
      {
        url: "/og.png",
        width: 1731,
        height: 909,
        alt: "JenAI robot workflow documentation",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    images: ["/og.png"],
  },
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
