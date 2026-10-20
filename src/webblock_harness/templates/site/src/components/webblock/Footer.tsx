type Link = { label: string; href: string };

export type FooterProps = { text?: string; links?: Link[] };

export function Footer({ text, links = [] }: FooterProps) {
  return (
    <footer className="wb-footer">
      <div className="wb-container wb-footer__inner">
        <p>{text}</p>
        <ul className="wb-footer__links">
          {links.map((link, i) => (
            <li key={i}>
              <a href={link.href}>{link.label}</a>
            </li>
          ))}
        </ul>
      </div>
    </footer>
  );
}
