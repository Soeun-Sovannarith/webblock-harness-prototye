import type { CSSProperties, ReactNode } from "react";

export type SiteRootProps = {
  children?: ReactNode;
  title?: string;
  description?: string;
  theme?: "light" | "dark" | "earth" | "clay";
  accentColor?: string;
};

export function SiteRoot({ children, theme = "light", accentColor }: SiteRootProps) {
  const style = accentColor ? ({ "--wb-accent": accentColor } as CSSProperties) : undefined;
  return (
    <div className="wb-site" data-theme={theme} style={style}>
      {children}
    </div>
  );
}
