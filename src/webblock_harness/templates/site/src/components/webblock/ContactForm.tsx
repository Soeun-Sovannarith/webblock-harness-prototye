"use client";

import { useState, type FormEvent } from "react";

export type ContactFormProps = { heading?: string; email?: string; submitLabel?: string; successMessage?: string };

/** Works without a backend: submitting opens the visitor's email app addressed to the business. */
export function ContactForm({ heading = "Contact us", email, submitLabel = "Send", successMessage }: ContactFormProps) {
  const [sent, setSent] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const subject = `Message from ${form.get("name") ?? "a visitor"}`;
    const body = `${form.get("message") ?? ""}\n\nReply to: ${form.get("from") ?? ""}`;
    if (email) window.location.href = `mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    setSent(true);
  }

  return (
    <section className="wb-section" id="contact">
      <div className="wb-container wb-narrow">
        <h2 className="wb-heading wb-heading--2">{heading}</h2>
        {sent ? (
          <p className="wb-notice">{successMessage ?? "Thank you!"}</p>
        ) : (
          <form className="wb-form" onSubmit={onSubmit}>
            <label>
              Name
              <input name="name" required autoComplete="name" />
            </label>
            <label>
              Email
              <input name="from" type="email" required autoComplete="email" />
            </label>
            <label>
              Message
              <textarea name="message" rows={4} required />
            </label>
            <button className="wb-button wb-button--primary" type="submit">
              {submitLabel}
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
