import { formatPrice, type Currency } from "./format";

export type ProductDescriptionProps = {
  title: string;
  body: string;
  materials?: string;
  price?: number;
  currency?: Currency;
};

export function ProductDescription({ title, body, materials, price, currency = "USD" }: ProductDescriptionProps) {
  return (
    <section className="wb-section" id="about">
      <div className="wb-container wb-prose">
        <h2 className="wb-heading wb-heading--2">{title}</h2>
        {body.split(/\n{2,}/).map((p, i) => (
          <p key={i} className="wb-text wb-text--lg">
            {p}
          </p>
        ))}
        {materials ? <p className="wb-muted">Materials: {materials}</p> : null}
        {price !== undefined ? <p className="wb-price">{formatPrice(price, currency)}</p> : null}
      </div>
    </section>
  );
}
