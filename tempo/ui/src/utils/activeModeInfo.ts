import { MODES } from "../components/modes";
import { BUILTIN_KITS, type KitInfo } from "../components/kits";

export function buildActiveModeInfo(
  activeKit: string | null,
  activeMode: string,
  customKits: KitInfo[],
) {
  if (activeKit) {
    const kit = [...BUILTIN_KITS, ...customKits].find(k => k.id === activeKit);
    if (!kit) return undefined;
    return {
      mode: `kit:${kit.id}`,
      label: kit.label,
      icon: kit.icon,
      tag: "kit",
      hint: kit.needsQuery ? "symbol or task to focus on" : undefined,
      argPrefix: kit.needsQuery ? "--query" : undefined,
      desc: kit.description,
    };
  }
  return MODES.find(m => m.mode === activeMode);
}
