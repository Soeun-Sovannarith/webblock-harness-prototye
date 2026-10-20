import type { ComponentType } from "react";

type Slot = ComponentType<Record<string, unknown>>;

export type ColumnsProps = { gap?: number; left: Slot; right: Slot };

export function Columns({ gap = 24, left: Left, right: Right }: ColumnsProps) {
  return (
    <div className="wb-container wb-block">
      <div className="wb-columns" style={{ gap }}>
        <div className="wb-columns__col">
          <Left />
        </div>
        <div className="wb-columns__col">
          <Right />
        </div>
      </div>
    </div>
  );
}
