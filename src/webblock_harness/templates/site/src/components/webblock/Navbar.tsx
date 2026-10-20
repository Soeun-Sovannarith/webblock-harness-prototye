type Link = { label: string; href: string };

export type NavbarProps = { brand: string; logo?: string; links?: Link[]; sticky?: boolean };

export function Navbar({ brand, logo, links = [], sticky = true }: NavbarProps) {
  return (
    <header className={sticky ? "wb-nav wb-nav--sticky" : "wb-nav"}>
      <div className="wb-container wb-nav__inner">
        <a className="wb-nav__brand" href="/">
          {logo ? <img src={logo} alt="" className="wb-nav__logo" /> : null}
          <span>{brand}</span>
        </a>
        <nav aria-label="Main">
          <ul className="wb-nav__links">
            {links.map((link, i) => (
              <li key={i}>
                <a href={link.href}>{link.label}</a>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
