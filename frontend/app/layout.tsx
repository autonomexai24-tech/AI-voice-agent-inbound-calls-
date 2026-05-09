import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RapidX AI Receptionist",
  description: "Tenant dashboard for multilingual AI receptionist operations",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
