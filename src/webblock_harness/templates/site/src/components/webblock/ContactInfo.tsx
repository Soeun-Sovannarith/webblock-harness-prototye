type Contact = { label: string; kind: string; value: string };

export type ContactInfoProps = {
  heading?: string;
  email?: string;
  phone?: string;
  address?: string;
  hours?: string;
  contacts?: Contact[];
  source?: "database" | "manual";
};

/** Only safe link targets are produced; anything else is shown as plain text. */
function href(kind: string, value: string): string | null {
  const v = value.trim();
  if (kind === "email" && /^[^@\s]+@[^@\s]+$/.test(v)) return `mailto:${v}`;
  if (kind === "phone" && /^\+?[0-9 ()-]{3,32}$/.test(v)) return `tel:${v.replace(/[^\d+]/g, "")}`;
  if (kind === "whatsapp") {
    const digits = v.replace(/\D/g, "");
    return digits.length >= 6 ? `https://wa.me/${digits}` : null;
  }
  if (kind === "telegram") {
    const handle = v.replace(/^@/, "").replace(/^https:\/\/t\.me\//, "");
    return /^[A-Za-z0-9_]{3,64}$/.test(handle) ? `https://t.me/${handle}` : null;
  }
  return /^https:\/\/[^\s<>"']+$/.test(v) ? v : null;
}

function Item({ label, kind, value }: Contact) {
  const target = href(kind, value);
  return (
    <li>
      <span className="wb-contact__label">{label}</span>
      {target ? (
        <a href={target} target={target.startsWith("https:") ? "_blank" : undefined} rel="noreferrer">
          {value}
        </a>
      ) : (
        <span>{value}</span>
      )}
    </li>
  );
}

export function ContactInfo({ heading = "Contact & location", email, phone, address, hours, contacts = [] }: ContactInfoProps) {
  const items: Contact[] = [
    ...(phone ? [{ label: "Phone", kind: "phone", value: phone }] : []),
    ...(email ? [{ label: "Email", kind: "email", value: email }] : []),
    ...contacts,
  ];
  return (
    <section className="wb-section" id="location">
      <div className="wb-container wb-contact">
        <div>
          <h2 className="wb-heading wb-heading--2">{heading}</h2>
          {address ? <p className="wb-text wb-text--lg">{address}</p> : null}
          {hours ? (
            <div className="wb-contact__hours">
              <h3 className="wb-heading wb-heading--4">Opening hours</h3>
              <p className="wb-text" style={{ whiteSpace: "pre-line" }}>{hours}</p>
            </div>
          ) : null}
        </div>
        {items.length ? (
          <ul className="wb-contact__list">
            {items.map((c, i) => (
              <Item key={i} {...c} />
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}
