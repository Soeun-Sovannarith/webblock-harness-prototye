import { formatPrice, type Currency } from "./format";

export type ProductCardProps = { name: string; description?: string; price: number; currency?: Currency; image?: string; badge?: string };

export function ProductCard({ name, description, price, currency = "USD", image, badge }: ProductCardProps) {
  return (
    <article className="wb-card">
      {image ? <img className="wb-card__image" src={image} alt={name} loading="lazy" /> : <div className="wb-card__image wb-card__image--empty" />}
      <div className="wb-card__body">
        {badge ? <span className="wb-badge">{badge}</span> : null}
        <h3 className="wb-card__title">{name}</h3>
        {description ? <p className="wb-muted">{description}</p> : null}
        <p className="wb-price">{formatPrice(price, currency)}</p>
      </div>
    </article>
  );
}

/** Stand-alone product card block (wrapped in a container). */
export function ProductCardBlock(props: ProductCardProps) {
  return (
    <div className="wb-container wb-block wb-card-single">
      <ProductCard {...props} />
    </div>
  );
}
