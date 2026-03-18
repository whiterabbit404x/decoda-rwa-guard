import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Phase 1 Treasury Control Dashboard",
  description: "Placeholder dashboards for tokenized treasury risk controls"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
