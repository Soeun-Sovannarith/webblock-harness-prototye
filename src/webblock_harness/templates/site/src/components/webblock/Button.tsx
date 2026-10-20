export type ButtonProps = { label: string; href: string; variant?: "primary" | "secondary" | "ghost" };

export function Button({ label, href, variant = "primary" }: ButtonProps) {
  return (
    <div className="wb-container wb-block wb-align-center">
      <a className={`wb-button wb-button--${variant}`} href={href}>
        {label}
      </a>
    </div>
  );
}
