import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pareto Commute · Boston",
  description: "Independent prototype. Not a City of Boston or MBTA service.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
