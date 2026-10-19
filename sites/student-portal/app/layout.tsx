import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CBVMS Student Portal",
  description: "Your campus records, appeals, and updates in one place.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
