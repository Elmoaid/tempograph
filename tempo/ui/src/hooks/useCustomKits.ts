import { useState, useEffect, useCallback } from "react";
import { readFile } from "../components/tempo";
import { BUILTIN_KITS, type KitInfo } from "../components/kits";

type KitSpec = { steps?: string[]; description?: string; needsQuery?: boolean };

export function parseCustomKits(raw: string): KitInfo[] {
  const parsed = JSON.parse(raw) as Record<string, KitSpec>;
  return Object.entries(parsed)
    .filter(([, spec]) => spec.steps && spec.steps.length > 0)
    .map(([id, spec]) => ({
      id,
      label: id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
      icon: BUILTIN_KITS[0].icon,
      description: spec.description || `Custom kit: ${spec.steps?.join(" + ")}`,
      needsQuery: spec.needsQuery,
    }));
}

export function useCustomKits(repoPath: string): {
  customKits: KitInfo[];
  loadCustomKits: () => void;
} {
  const [customKits, setCustomKits] = useState<KitInfo[]>([]);

  const loadCustomKits = useCallback(() => {
    if (!repoPath) return;
    readFile(`${repoPath}/.tempo/kits.json`).then(r => {
      if (!r.success || !r.output) return;
      try {
        setCustomKits(parseCustomKits(r.output));
      } catch {
        // malformed kits.json — silently ignore
      }
    });
  }, [repoPath]);

  useEffect(() => { loadCustomKits(); }, [loadCustomKits]);

  return { customKits, loadCustomKits };
}
