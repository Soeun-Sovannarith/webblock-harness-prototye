import { Render } from "@puckeditor/core";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import type { Api, ApiError, FieldSpec, Registry, Template } from "../api";
import { navigate } from "../App";
import { buildPuckConfig } from "../puckConfig";

type ProductRow = { name: string; price: string; description: string; image: string };
const emptyProduct: ProductRow = { name: "", price: "", description: "", image: "" };

export function CreateSite({ api }: { api: Api }) {
  const [step, setStep] = useState<1 | 2>(1);
  const [form, setForm] = useState({
    business_name: "", business_type: "", tagline: "", description: "", logo_url: "", email: "", phone: "", address: "",
    opening_hours: "", currency: "USD",
  });
  const [businessTypes, setBusinessTypes] = useState<string[]>([]);
  const [products, setProducts] = useState<ProductRow[]>([{ ...emptyProduct }]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [chosen, setChosen] = useState<string>("artisan-shop");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.templates().then(setTemplates, (e: ApiError) => setError(e.message));
    api.businessTypes().then(setBusinessTypes, () => setBusinessTypes([]));
  }, [api]);

  const update = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });

  function next(e: FormEvent) {
    e.preventDefault();
    setStep(2);
  }

  async function create() {
    setBusy(true);
    setError(null);
    const profile = {
      ...Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim() || undefined])),
      business_name: form.business_name.trim(),
      products: products
        .filter((p) => p.name.trim() && p.price.trim())
        .map((p) => ({
          name: p.name.trim(),
          price: Number(p.price),
          ...(p.description.trim() ? { description: p.description.trim() } : {}),
          ...(p.image.trim() ? { image: p.image.trim() } : {}),
        })),
    };
    try {
      const project = await api.createSite(chosen, profile);
      navigate(`#/edit/${project.id}`);
    } catch (err) {
      const apiErr = err as ApiError;
      setError(apiErr.message + (apiErr.details?.errors ? ` ${JSON.stringify(apiErr.details.errors)}` : ""));
      setBusy(false);
    }
  }

  return (
    <div className="app-page">
      <header className="topbar">
        <button className="btn ghost small" onClick={() => navigate("#/")}>
          ← Your websites
        </button>
        <span className="brand">New website</span>
        <span className="steps">
          <span className={step === 1 ? "step active" : "step"}>1 · Your business</span>
          <span className={step === 2 ? "step active" : "step"}>2 · Template</span>
        </span>
      </header>
      <main className="container">
        {step === 1 ? (
          <form className="panel form-grid" onSubmit={next}>
            <h1 className="display span-2">Tell us about your business</h1>
            <label className="field span-2">
              Business name *
              <input required maxLength={80} value={form.business_name} onChange={update("business_name")} placeholder="Kampot Clay Studio" />
            </label>
            <label className="field">
              Business type
              <select value={form.business_type} onChange={update("business_type")}>
                <option value="">Choose…</option>
                {businessTypes.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
            <label className="field">
              Logo URL (optional)
              <input maxLength={500} placeholder="https://…" value={form.logo_url} onChange={update("logo_url")} />
            </label>
            <label className="field span-2">
              Tagline
              <input maxLength={120} value={form.tagline} onChange={update("tagline")} placeholder="Handmade pottery from Kampot" />
            </label>
            <label className="field span-2">
              Short description
              <textarea maxLength={1000} rows={3} value={form.description} onChange={update("description")} />
            </label>
            <label className="field">
              Email
              <input type="email" maxLength={200} value={form.email} onChange={update("email")} />
            </label>
            <label className="field">
              Phone
              <input maxLength={40} value={form.phone} onChange={update("phone")} />
            </label>
            <label className="field">
              Address
              <input maxLength={200} value={form.address} onChange={update("address")} />
            </label>
            <label className="field">
              Opening hours
              <textarea rows={2} maxLength={1000} placeholder={"Mon–Fri 8:00–18:00\nSat 9:00–14:00"} value={form.opening_hours} onChange={update("opening_hours")} />
            </label>
            <label className="field">
              Currency
              <select value={form.currency} onChange={update("currency")}>
                <option value="USD">US dollar ($)</option>
                <option value="KHR">Cambodian riel (៛)</option>
              </select>
            </label>
            <fieldset className="span-2 products">
              <legend>Products (optional — templates include examples you can edit later)</legend>
              {products.map((p, i) => (
                <div key={i} className="product-row">
                  <input placeholder="Name" maxLength={120} value={p.name}
                    onChange={(e) => setProducts(products.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                  <input placeholder="Price" type="number" min={0} step="0.01" value={p.price}
                    onChange={(e) => setProducts(products.map((x, j) => (j === i ? { ...x, price: e.target.value } : x)))} />
                  <input placeholder="Description" maxLength={1000} value={p.description}
                    onChange={(e) => setProducts(products.map((x, j) => (j === i ? { ...x, description: e.target.value } : x)))} />
                  <input placeholder="Image URL (https://…)" maxLength={500} value={p.image}
                    onChange={(e) => setProducts(products.map((x, j) => (j === i ? { ...x, image: e.target.value } : x)))} />
                  <button type="button" className="btn ghost small" onClick={() => setProducts(products.filter((_, j) => j !== i))}>
                    Remove
                  </button>
                </div>
              ))}
              <button type="button" className="btn ghost small" onClick={() => setProducts([...products, { ...emptyProduct }])}>
                + Add product
              </button>
            </fieldset>
            <div className="span-2 row end">
              <button className="btn primary">Choose a template →</button>
            </div>
          </form>
        ) : (
          <section>
            <div className="page-head">
              <div>
                <h1 className="display">Choose a template</h1>
                <p className="muted">You can change every text, image, price and section in the editor next.</p>
              </div>
              <div className="row">
                <button className="btn ghost" onClick={() => setStep(1)}>
                  ← Back
                </button>
                <button className="btn primary" disabled={busy} onClick={create}>
                  {busy ? "Creating…" : "Create & open editor"}
                </button>
              </div>
            </div>
            {error ? <p className="error">{error}</p> : null}
            <div className="cards templates">
              {templates.map((t) => (
                <TemplateCard key={t.id} template={t} selected={chosen === t.id} onSelect={() => setChosen(t.id)} />
              ))}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

function TemplateCard({ template, selected, onSelect }: { template: Template; selected: boolean; onSelect: () => void }) {
  const config = useMemo(() => buildPuckConfig(previewRegistry), []);
  const page = template.preview.pages["/"];
  return (
    <button type="button" className={selected ? "panel template selected" : "panel template"} onClick={onSelect} aria-pressed={selected}>
      <div className="template__preview" aria-hidden="true">
        <div className="template__scale">
          <Render config={config} data={page} />
        </div>
      </div>
      <div className="template__meta">
        <h2>{template.name}</h2>
        <p className="muted small">{template.description}</p>
        <p className="small">Best for: {template.best_for}</p>
      </div>
    </button>
  );
}

/** Previews only need render functions; an empty-field registry stub is enough. */
const previewRegistry: Registry = {
  hash: "preview",
  root: { name: "Root", label: "Page", description: "", fields: {}, default_props: {} },
  components: ["Navbar", "Hero", "Heading", "Text", "Button", "Image", "ProductDescription", "ProductGallery", "ProductCard",
    "Testimonial", "ContactForm", "Columns", "Spacer", "Footer"].map((name) => ({
    name, label: name, description: "", default_props: {},
    fields: (name === "Columns" ? { left: { type: "slot" }, right: { type: "slot" } } : {}) as Record<string, FieldSpec>,
  })),
};
