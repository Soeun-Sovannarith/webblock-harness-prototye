export type ImageProps = { src: string; alt: string; rounded?: boolean };

export function Image({ src, alt, rounded = true }: ImageProps) {
  return (
    <div className="wb-container wb-block">
      <img className={rounded ? "wb-image wb-image--rounded" : "wb-image"} src={src} alt={alt} loading="lazy" />
    </div>
  );
}
