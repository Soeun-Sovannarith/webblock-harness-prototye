export type TextProps = { text: string; align?: "left" | "center" | "right"; size?: "sm" | "md" | "lg" };

export function Text({ text, align = "left", size = "md" }: TextProps) {
  return (
    <div className={`wb-container wb-block wb-align-${align}`}>
      {text.split(/\n{2,}/).map((paragraph, i) => (
        <p key={i} className={`wb-text wb-text--${size}`}>
          {paragraph}
        </p>
      ))}
    </div>
  );
}
