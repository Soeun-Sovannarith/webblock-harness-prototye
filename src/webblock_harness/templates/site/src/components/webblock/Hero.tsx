export type HeroProps = {
  heading: string;
  subheading?: string;
  image?: string;
  ctaLabel?: string;
  ctaHref?: string;
  align?: "left" | "center" | "right";
  backgroundColor?: string;
  paddingY?: number;
};

export function Hero({ heading, subheading, image, ctaLabel, ctaHref, align = "center", backgroundColor, paddingY = 64 }: HeroProps) {
  return (
    <section
      className={`wb-hero wb-align-${align}${image ? " wb-hero--image" : ""}`}
      style={{
        paddingTop: paddingY,
        paddingBottom: paddingY,
        ...(backgroundColor ? { background: backgroundColor } : {}),
        ...(image ? { backgroundImage: `linear-gradient(rgba(0,0,0,.45), rgba(0,0,0,.45)), url(${JSON.stringify(image)})` } : {}),
      }}
    >
      <div className="wb-container">
        <h1 className="wb-hero__title">{heading}</h1>
        {subheading ? <p className="wb-hero__subtitle">{subheading}</p> : null}
        {ctaLabel && ctaHref ? (
          <a className="wb-button wb-button--primary" href={ctaHref}>
            {ctaLabel}
          </a>
        ) : null}
      </div>
    </section>
  );
}
