import { useState, useEffect, useCallback } from "react";
import { pathExists, readFile, runTempo } from "../components/tempo";

interface AmbientStatus {
  exists: boolean;
  timestamp: string | null;
  generating: boolean;
}

export function useAmbientStatus(repoPath: string) {
  const [status, setStatus] = useState<AmbientStatus>({
    exists: false,
    timestamp: null,
    generating: false,
  });

  const checkStatus = useCallback(async () => {
    if (!repoPath) return;
    const contextFile = `${repoPath}/.tempograph-context.md`;
    const exists = await pathExists(contextFile);
    if (!exists) {
      setStatus({ exists: false, timestamp: null, generating: false });
      return;
    }
    const result = await readFile(contextFile);
    let timestamp: string | null = null;
    if (result.success && result.output) {
      const match = result.output.match(/Generated:\s*([^|]+)/);
      if (match) timestamp = match[1].trim();
    }
    setStatus({ exists: true, timestamp, generating: false });
  }, [repoPath]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- checkStatus is async; setState is called after await
    checkStatus();
  }, [checkStatus]);

  const generate = useCallback(async () => {
    if (!repoPath || status.generating) return;
    setStatus((s) => ({ ...s, generating: true }));
    await runTempo(repoPath, "ambient");
    await checkStatus();
  }, [repoPath, status.generating, checkStatus]);

  return { status, generate, refresh: checkStatus };
}
