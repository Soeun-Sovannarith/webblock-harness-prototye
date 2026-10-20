export type SpacerProps = { size?: number };

export function Spacer({ size = 32 }: SpacerProps) {
  return <div aria-hidden="true" style={{ height: size }} />;
}
