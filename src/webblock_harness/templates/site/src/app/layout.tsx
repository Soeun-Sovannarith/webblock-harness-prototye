import type { Metadata } from "next";
import type { ReactNode } from "react";

import { site } from "../site";
import "./globals.css";

const home = site.pages["/"]?.root?.props as { title?: string; description?: string } | undefined;

export const metadata: Metadata = {
  title: home?.title ?? "__SITE_NAME__",
  description: home?.description ?? "",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
