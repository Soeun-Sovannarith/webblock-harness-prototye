/**
 * Builds the Puck editor configuration from the harness component registry.
 * Field definitions come from the registry (the same schema the harness validates against);
 * rendering uses the exact component files that are pushed to the client's repository.
 */
import type { ComponentConfig, Config, Field, Fields } from "@puckeditor/core";

import { components, SiteRoot } from "@site/components/webblock";

import type { ComponentSpec, FieldSpec, Registry } from "./api";

const CATEGORIES: Record<string, string[]> = {
  "Page structure": ["Navbar", "Hero", "Footer", "Columns", "Spacer"],
  Content: ["Heading", "Text", "Image", "Button", "Testimonial"],
  Shop: ["ProductGallery", "ProductCard", "ProductDescription"],
  Business: ["ContactInfo", "ContactForm"],
};

function humanize(name: string): string {
  return name.replace(/([A-Z])/g, " $1").replace(/^./, (c) => c.toUpperCase()).trim();
}

function toField(name: string, spec: FieldSpec): Field {
  const label = spec.label ?? humanize(name) + (spec.required ? " *" : "");
  switch (spec.type) {
    case "textarea":
      return { type: "textarea", label };
    case "number":
      return { type: "number", label, min: spec.min ?? undefined, max: spec.max ?? undefined };
    case "boolean":
      return { type: "radio", label, options: [{ label: "Yes", value: true }, { label: "No", value: false }] };
    case "select":
      return { type: "select", label, options: (spec.options ?? []).map((v) => ({ label: String(v), value: v })) };
    case "array": {
      const itemFields = spec.item_fields ?? {};
      const defaults = Object.fromEntries(
        Object.entries(itemFields).filter(([, f]) => f.required).map(([k, f]) => [k, f.type === "number" ? 0 : "New item"]),
      );
      return {
        type: "array",
        label,
        arrayFields: toFields(itemFields),
        defaultItemProps: defaults,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        getItemSummary: (item: any, i?: number) => item?.name || item?.label || `Item ${(i ?? 0) + 1}`,
      };
    }
    case "object":
      return { type: "object", label, objectFields: toFields(spec.item_fields ?? {}) };
    case "slot":
      return { type: "slot", label };
    default: // text, url, image, color
      return {
        type: "text",
        label: spec.type === "image" ? `${label} (image URL)` : spec.type === "color" ? `${label} (#hex)` : label,
      };
  }
}

function toFields(specs: Record<string, FieldSpec>): Fields {
  return Object.fromEntries(Object.entries(specs).map(([name, spec]) => [name, toField(name, spec)]));
}

// Puck passes editor-only props; components only need their content props.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function contentProps({ puck: _puck, editMode: _editMode, ...props }: Record<string, any>) {
  return props;
}

export function buildPuckConfig(registry: Registry): Config {
  const known = new Set(registry.components.map((c) => c.name));
  const configComponents: Record<string, ComponentConfig> = {};
  for (const spec of registry.components as ComponentSpec[]) {
    const Component = components[spec.name];
    if (!Component) continue; // project-specific components render only in the deployed site
    configComponents[spec.name] = {
      label: spec.label,
      fields: toFields(spec.fields),
      defaultProps: spec.default_props,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      render: (props: any) => <Component {...contentProps(props)} />,
    };
  }
  const categories = Object.fromEntries(
    Object.entries(CATEGORIES).map(([title, names]) => [title, { title, components: names.filter((n) => known.has(n) && configComponents[n]) }]),
  );
  return {
    root: {
      fields: toFields(registry.root.fields),
      defaultProps: registry.root.default_props,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      render: (props: any) => <SiteRoot {...contentProps(props)} />,
    },
    categories,
    components: configComponents,
  };
}
