import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./portal.css";

export const metadata: Metadata = { title: "Business Portal", robots: { index: false, follow: false } };

export default function BusinessLayout({ children }: { children: ReactNode }) {
  return <div className="bp">{children}</div>;
}
