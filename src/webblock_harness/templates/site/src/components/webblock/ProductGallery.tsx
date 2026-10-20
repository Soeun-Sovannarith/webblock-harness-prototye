import type { Currency } from "./format";
import { ProductCard } from "./ProductCard";

type Product = { name: string; description?: string; price: number; image?: string; badge?: string };

export type ProductGalleryProps = { title?: string; columns?: 2 | 3 | 4; currency?: Currency; products?: Product[] };

export function ProductGallery({ title, columns = 3, currency = "USD", products = [] }: ProductGalleryProps) {
  return (
    <section className="wb-section" id="products">
      <div className="wb-container">
        {title ? <h2 className="wb-heading wb-heading--2 wb-align-center">{title}</h2> : null}
        <div className={`wb-grid wb-grid--${columns}`}>
          {products.map((product, i) => (
            <ProductCard key={i} {...product} currency={currency} />
          ))}
        </div>
      </div>
    </section>
  );
}
