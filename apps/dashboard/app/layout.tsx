import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kairos",
  description: "RAG observability and agent reliability testing",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
