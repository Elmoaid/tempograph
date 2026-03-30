import { useState, useRef, useCallback, useEffect } from "react";
import { save as saveDialog } from "@tauri-apps/plugin-dialog";
import { saveOutput } from "../components/tempo";

export interface UseOutputActionsResult {
  copied: boolean;
  saved: boolean;
  copyOutput: () => void;
  handleSaveOutput: () => Promise<void>;
  saveOutputRef: React.MutableRefObject<(() => Promise<void>) | null>;
}

export function useOutputActions(
  modeOutput: string,
  activeMode: string,
  activeKit: string | null,
): UseOutputActionsResult {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const saveOutputRef = useRef<(() => Promise<void>) | null>(null);

  const copyOutput = useCallback(() => {
    navigator.clipboard.writeText(modeOutput);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [modeOutput]);

  const handleSaveOutput = useCallback(async () => {
    if (!modeOutput) return;
    const label = activeKit ? `kit-${activeKit}` : activeMode;
    const date = new Date().toISOString().slice(0, 10);
    const defaultName = `tempograph-${label}-${date}.txt`;
    const chosenPath = await saveDialog({
      defaultPath: defaultName,
      filters: [{ name: "Text", extensions: ["txt"] }],
    });
    if (!chosenPath) return;
    await saveOutput(chosenPath, modeOutput);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }, [modeOutput, activeMode, activeKit]);

  useEffect(() => { saveOutputRef.current = handleSaveOutput; }, [handleSaveOutput]);

  return { copied, saved, copyOutput, handleSaveOutput, saveOutputRef };
}
