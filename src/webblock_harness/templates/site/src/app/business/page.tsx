"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import customFieldsJson from "../../../webblock/custom-fields.json";
import siteJson from "../../../webblock/site.json";

type FieldDef = { key: string; label: string; type: "text" | "number" | "boolean" | "select"; options?: string[]; required?: boolean };
type Product = {
  id: string; title: string; price: number; status: "ACTIVE" | "HIDDEN"; description?: string; imageUrl?: string; badge?: string;
  customFields: Record<string, unknown>;
};
type Profile = {
  businessName: string; businessType: string | null; description: string | null; logoUrl: string | null; email: string | null;
  phone: string | null; address: string | null; openingHours: string | null; currency: string;
};
type Contact = { id: string; label: string; kind: string; value: string };
type ApiError = { status: number; message: string; details?: { path: string; message: string }[] };

const FIELDS = (customFieldsJson as FieldDef[]).filter((f) => !["description", "imageUrl", "badge"].includes(f.key));
const KINDS = ["phone", "email", "whatsapp", "telegram", "facebook", "instagram", "website", "other"];
const TOKEN_KEY = "webblock.portal.token";

function readToken(): string | null {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("token");
  if (fromHash) {
    try { sessionStorage.setItem(TOKEN_KEY, fromHash); } catch { /* private mode: keep in memory only */ }
    window.history.replaceState(null, "", window.location.pathname); // never leave the token in the address bar
    return fromHash;
  }
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}

async function call<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw { status: res.status, message: data?.error?.message ?? res.statusText, details: data?.error?.details } as ApiError;
  return data as T;
}

function describe(err: unknown): string {
  const e = err as ApiError;
  return e.details?.length ? `${e.message} ${e.details.map((d) => `${d.path}: ${d.message}`).join("; ")}` : e.message;
}

export default function BusinessPortal() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<"products" | "business" | "contacts">("products");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    setToken(readToken());
    setReady(true);
  }, []);

  // Stable callbacks: child sections reload data only when the token changes, not on every notice.
  const onDone = useCallback((text: string) => setNotice({ kind: "ok", text }), []);
  const onError = useCallback((err: unknown) => {
    if ((err as ApiError).status === 401) {
      try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
      setToken(null);
    }
    setNotice({ kind: "error", text: describe(err) });
  }, []);

  if (!ready) return null;
  if (!token) {
    return (
      <main className="bp-center">
        <div className="bp-card bp-narrow">
          <p className="bp-eyebrow">{siteJson.siteName}</p>
          <h1>Business Portal</h1>
          <p className="bp-muted">Open the Business Portal from your WebBlock dashboard to manage products and contact details.</p>
        </div>
      </main>
    );
  }

  return (
    <div className="bp-shell">
      <header className="bp-top">
        <div>
          <p className="bp-eyebrow">Business Portal</p>
          <strong>{siteJson.siteName}</strong>
        </div>
        <nav className="bp-tabs" aria-label="Sections">
          {(["products", "business", "contacts"] as const).map((t) => (
            <button key={t} className={tab === t ? "bp-tab active" : "bp-tab"} onClick={() => { setTab(t); setNotice(null); }}>
              {t === "products" ? "Products" : t === "business" ? "Business info" : "Contacts"}
            </button>
          ))}
        </nav>
        <a className="bp-btn ghost small" href="/" target="_blank" rel="noreferrer">View website ↗</a>
      </header>
      {notice ? <p className={`bp-notice ${notice.kind}`} role="status">{notice.text}</p> : null}
      <main className="bp-main">
        {tab === "products" ? <Products token={token} onDone={onDone} onError={onError} /> : null}
        {tab === "business" ? <Business token={token} onDone={onDone} onError={onError} /> : null}
        {tab === "contacts" ? <Contacts token={token} onDone={onDone} onError={onError} /> : null}
      </main>
    </div>
  );
}

type SectionProps = { token: string; onDone: (text: string) => void; onError: (err: unknown) => void };

const emptyProduct = { title: "", price: "", description: "", imageUrl: "", badge: "", status: "ACTIVE" as const, custom: {} as Record<string, unknown> };

function Products({ token, onDone, onError }: SectionProps) {
  const [items, setItems] = useState<Product[] | null>(null);
  const [form, setForm] = useState(emptyProduct);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const load = useCallback(() => call<{ products: Product[] }>(token, "GET", "/api/products?include=all").then((d) => setItems(d.products), onError), [token, onError]);
  useEffect(() => { void load(); }, [load]);

  function edit(p: Product) {
    setEditing(p.id);
    setForm({ title: p.title, price: String(p.price), description: p.description ?? "", imageUrl: p.imageUrl ?? "", badge: p.badge ?? "",
      status: p.status as "ACTIVE", custom: { ...p.customFields } });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const customFields = Object.fromEntries(FIELDS.map((f) => {
      const v = form.custom[f.key];
      if (v === "" || v === undefined) return [f.key, null];
      return [f.key, f.type === "number" ? Number(v) : v];
    }));
    const body = { title: form.title, price: Number(form.price), description: form.description, imageUrl: form.imageUrl,
      badge: form.badge, status: form.status, ...(FIELDS.length ? { customFields } : {}) };
    try {
      if (editing) await call(token, "PATCH", `/api/products/${editing}`, body);
      else await call(token, "POST", "/api/products", body);
      onDone(editing ? "Product updated — your website shows it now." : "Product added — your website shows it now.");
      setForm(emptyProduct);
      setEditing(null);
      await load();
    } catch (err) { onError(err); }
  }

  async function toggle(p: Product) {
    try {
      await call(token, "PATCH", `/api/products/${p.id}`, { status: p.status === "ACTIVE" ? "HIDDEN" : "ACTIVE" });
      await load();
    } catch (err) { onError(err); }
  }

  async function remove(id: string) {
    try {
      await call(token, "DELETE", `/api/products/${id}`);
      setConfirmDelete(null);
      onDone("Product deleted.");
      await load();
    } catch (err) { onError(err); }
  }

  return (
    <div className="bp-grid">
      <section className="bp-card">
        <h2>Products {items ? <span className="bp-muted">({items.length})</span> : null}</h2>
        {items === null ? <p className="bp-muted">Loading…</p> : null}
        {items?.length === 0 ? <p className="bp-muted">No products yet. Add your first one.</p> : null}
        <ul className="bp-list">
          {items?.map((p) => (
            <li key={p.id} className={p.status === "HIDDEN" ? "hidden" : ""}>
              {p.imageUrl ? <img src={p.imageUrl} alt="" /> : <span className="bp-thumb" />}
              <div className="bp-grow">
                <strong>{p.title}</strong>
                <span className="bp-muted"> · {p.price.toFixed(2)}{p.status === "HIDDEN" ? " · hidden" : ""}</span>
              </div>
              <button className="bp-btn ghost small" onClick={() => edit(p)}>Edit</button>
              <button className="bp-btn ghost small" onClick={() => void toggle(p)}>{p.status === "ACTIVE" ? "Hide" : "Show"}</button>
              {confirmDelete === p.id ? (
                <button className="bp-btn danger small" onClick={() => void remove(p.id)}>Confirm delete</button>
              ) : (
                <button className="bp-btn ghost small" onClick={() => setConfirmDelete(p.id)}>Delete</button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <form className="bp-card bp-form" onSubmit={submit}>
        <h2>{editing ? "Edit product" : "Add a product"}</h2>
        <label>Name *<input required maxLength={255} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
        <label>Price *<input required type="number" min={0} step="0.01" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} /></label>
        <label>Description<textarea rows={3} maxLength={1000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
        <label>Image URL<input placeholder="https://…" maxLength={500} value={form.imageUrl} onChange={(e) => setForm({ ...form, imageUrl: e.target.value })} /></label>
        <label>Badge<input maxLength={30} placeholder="New, Bestseller…" value={form.badge} onChange={(e) => setForm({ ...form, badge: e.target.value })} /></label>
        {FIELDS.map((f) => (
          <label key={f.key}>
            {f.label}{f.required ? " *" : ""}
            {f.type === "select" ? (
              <select required={f.required} value={String(form.custom[f.key] ?? "")} onChange={(e) => setForm({ ...form, custom: { ...form.custom, [f.key]: e.target.value } })}>
                <option value="">—</option>
                {f.options?.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : f.type === "boolean" ? (
              <select value={form.custom[f.key] === true ? "yes" : form.custom[f.key] === false ? "no" : ""}
                onChange={(e) => setForm({ ...form, custom: { ...form.custom, [f.key]: e.target.value === "" ? "" : e.target.value === "yes" } })}>
                <option value="">—</option><option value="yes">Yes</option><option value="no">No</option>
              </select>
            ) : (
              <input required={f.required} type={f.type === "number" ? "number" : "text"} value={String(form.custom[f.key] ?? "")}
                onChange={(e) => setForm({ ...form, custom: { ...form.custom, [f.key]: e.target.value } })} />
            )}
          </label>
        ))}
        <div className="bp-row">
          <button className="bp-btn primary">{editing ? "Save changes" : "Add product"}</button>
          {editing ? <button type="button" className="bp-btn ghost" onClick={() => { setEditing(null); setForm(emptyProduct); }}>Cancel</button> : null}
        </div>
      </form>
    </div>
  );
}

function Business({ token, onDone, onError }: SectionProps) {
  const [profile, setProfile] = useState<Profile | null>(null);

  useEffect(() => {
    call<{ profile: Profile | null }>(token, "GET", "/api/business").then(
      (d) => setProfile(d.profile ?? { businessName: siteJson.siteName, businessType: null, description: null, logoUrl: null, email: null,
        phone: null, address: null, openingHours: null, currency: "USD" }),
      onError,
    );
  }, [token, onError]);

  if (!profile) return <p className="bp-muted">Loading…</p>;
  const set = (key: keyof Profile) => (e: { target: { value: string } }) => setProfile({ ...profile, [key]: e.target.value });

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await call(token, "PUT", "/api/business", { ...profile, currency: profile!.currency as "USD" });
      onDone("Business information saved — your website shows it now.");
    } catch (err) { onError(err); }
  }

  return (
    <form className="bp-card bp-form bp-narrow-form" onSubmit={save}>
      <h2>Business information</h2>
      <label>Business name *<input required maxLength={80} value={profile.businessName} onChange={set("businessName")} /></label>
      <label>Business type<input maxLength={60} value={profile.businessType ?? ""} onChange={set("businessType")} /></label>
      <label>Description<textarea rows={3} maxLength={1000} value={profile.description ?? ""} onChange={set("description")} /></label>
      <label>Logo URL<input maxLength={500} placeholder="https://…" value={profile.logoUrl ?? ""} onChange={set("logoUrl")} /></label>
      <div className="bp-two">
        <label>Email<input type="email" maxLength={200} value={profile.email ?? ""} onChange={set("email")} /></label>
        <label>Phone<input maxLength={40} value={profile.phone ?? ""} onChange={set("phone")} /></label>
      </div>
      <label>Address<input maxLength={300} value={profile.address ?? ""} onChange={set("address")} /></label>
      <label>Opening hours<textarea rows={4} maxLength={1000} placeholder={"Mon–Fri 8:00–18:00\nSat 9:00–14:00"} value={profile.openingHours ?? ""} onChange={set("openingHours")} /></label>
      <label>Currency
        <select value={profile.currency} onChange={set("currency")}><option value="USD">US dollar ($)</option><option value="KHR">Cambodian riel (៛)</option></select>
      </label>
      <div className="bp-row"><button className="bp-btn primary">Save</button></div>
    </form>
  );
}

function Contacts({ token, onDone, onError }: SectionProps) {
  const [items, setItems] = useState<Contact[] | null>(null);
  const [form, setForm] = useState({ label: "", kind: "phone", value: "" });
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const load = useCallback(() => call<{ contacts: Contact[] }>(token, "GET", "/api/business").then((d) => setItems(d.contacts), onError), [token, onError]);
  useEffect(() => { void load(); }, [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await call(token, "POST", "/api/business/contacts", form);
      setForm({ label: "", kind: form.kind, value: "" });
      onDone("Contact added.");
      await load();
    } catch (err) { onError(err); }
  }

  async function remove(id: string) {
    try {
      await call(token, "DELETE", `/api/business/contacts/${id}`);
      setConfirmDelete(null);
      onDone("Contact removed.");
      await load();
    } catch (err) { onError(err); }
  }

  return (
    <div className="bp-grid">
      <section className="bp-card">
        <h2>Contact methods</h2>
        {items?.length === 0 ? <p className="bp-muted">No extra contacts yet (phone and email from Business info are always shown).</p> : null}
        <ul className="bp-list">
          {items?.map((c) => (
            <li key={c.id}>
              <div className="bp-grow"><strong>{c.label}</strong> <span className="bp-muted">· {c.kind} · {c.value}</span></div>
              {confirmDelete === c.id ? (
                <button className="bp-btn danger small" onClick={() => void remove(c.id)}>Confirm delete</button>
              ) : (
                <button className="bp-btn ghost small" onClick={() => setConfirmDelete(c.id)}>Delete</button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <form className="bp-card bp-form" onSubmit={add}>
        <h2>Add a contact</h2>
        <label>Label *<input required maxLength={60} placeholder="Orders, Telegram, Facebook page…" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></label>
        <label>Type<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></label>
        <label>Value *<input required maxLength={300} placeholder={form.kind === "website" || form.kind === "facebook" || form.kind === "instagram" ? "https://…" : ""} value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} /></label>
        <div className="bp-row"><button className="bp-btn primary">Add contact</button></div>
      </form>
    </div>
  );
}
