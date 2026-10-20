export type TestimonialProps = { quote: string; author: string };

export function Testimonial({ quote, author }: TestimonialProps) {
  return (
    <section className="wb-section wb-section--alt">
      <figure className="wb-container wb-quote">
        <blockquote>“{quote}”</blockquote>
        <figcaption>— {author}</figcaption>
      </figure>
    </section>
  );
}
