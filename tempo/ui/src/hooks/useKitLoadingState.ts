import { BUILTIN_KITS, type KitInfo } from "../components/kits";
import { useCustomKits } from "./useCustomKits";

export function useKitLoadingState(repoPath: string): {
  customKits: KitInfo[];
  loadCustomKits: () => void;
  allKits: KitInfo[];
} {
  const { customKits, loadCustomKits } = useCustomKits(repoPath);
  const allKits = [...BUILTIN_KITS, ...customKits];
  return { customKits, loadCustomKits, allKits };
}
