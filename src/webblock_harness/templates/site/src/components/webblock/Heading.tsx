export type HeadingProps = { text: string; level?: 1 | 2 | 3 | 4; align?: "left" | "center" | "right"; color?: string };

export function Heading({ text, level = 2, align = "left", color }: HeadingProps) {
  const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4";
  return (
    <div className={`wb-container wb-block wb-align-${align}`}>
      <Tag className={`wb-heading wb-heading--${level}`} style={color ? { color } : undefined}>
        {text}
      </Tag>
    </div>
  );
}
