import type { Config, Fields } from "@puckeditor/core";

import { components, SiteRoot, slotFields, type SiteRootProps } from "./components/webblock";

// Puck adds editor-only props (`puck`, `editMode`) that hold functions; they must not reach client components.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function contentProps({ puck: _puck, editMode: _editMode, ...props }: Record<string, any>) {
  return props;
}

function fieldsFor(name: string): Fields {
  return Object.fromEntries((slotFields[name] ?? []).map((slot) => [slot, { type: "slot" as const }]));
}

/**
 * Render-only Puck configuration. Editable field definitions live in the WebBlock component registry
 * (the editor builds them from it); only slot fields are declared here because rendering needs them.
 */
export const puckConfig: Config = {
  root: { render: (props: SiteRootProps) => <SiteRoot {...(contentProps(props) as SiteRootProps)} /> },
  components: Object.fromEntries(
    Object.entries(components).map(([name, Component]) => [
      name,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { fields: fieldsFor(name), render: (props: any) => <Component {...contentProps(props)} /> },
    ]),
  ),
};
