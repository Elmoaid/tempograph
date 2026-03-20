import type { TempoResult } from "../App";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let invoke: ((cmd: string, args?: Record<string, unknown>) => Promise<any>) | null = null;

const _fallback = async () => ({
  success: false,
  output: "",
  mode: "",
});

async function getInvoke() {
  if (invoke) return invoke;
  // Check if Tauri runtime is available via window globals (instant, no async)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (!(window as any).__TAURI_INTERNALS__) {
    invoke = _fallback;
    return invoke;
  }
  try {
    const tauri = await import("@tauri-apps/api/core");
    if (typeof tauri.invoke !== "function") throw new Error("no invoke");
    invoke = tauri.invoke;
  } catch {
    invoke = _fallback;
  }
  return invoke;
}

export async function runTempo(
  repoPath: string,
  mode: string,
  extraArgs: string[] = []
): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("run_tempo", { repoPath, mode, extraArgs });
  } catch (e) {
    return { success: false, output: String(e), mode };
  }
}

export async function readConfig(repoPath: string) {
  const fn = await getInvoke();
  try {
    return await fn("read_config", { repoPath });
  } catch (e) {
    return { success: false, data: {}, path: "", error: String(e) };
  }
}

export async function writeConfig(
  repoPath: string,
  config: Record<string, unknown>
) {
  const fn = await getInvoke();
  try {
    return await fn("write_config", { repoPath, config });
  } catch (e) {
    return { success: false, data: {}, path: "", error: String(e) };
  }
}

export async function listNotes(repoPath: string) {
  const fn = await getInvoke();
  try {
    const result = await fn("list_notes", { repoPath });
    return Array.isArray(result) ? result : [];
  } catch {
    return [];
  }
}

export async function readFile(path: string): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("read_file", { path });
  } catch (e) {
    return { success: false, output: String(e), mode: "file" };
  }
}

export async function readTelemetry(repoPath: string): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("read_telemetry", { repoPath });
  } catch (e) {
    return { success: false, output: String(e), mode: "telemetry" };
  }
}

export async function getRepoInfo(repoPath: string): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("get_repo_info", { repoPath });
  } catch (e) {
    return { success: false, output: String(e), mode: "info" };
  }
}

export async function detectRepo(): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("detect_repo");
  } catch (e) {
    return { success: false, output: "", mode: "detect" };
  }
}

export async function gitInfo(repoPath: string): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("git_info", { repoPath });
  } catch (e) {
    return { success: false, output: String(e), mode: "git" };
  }
}

interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: string | null;
}

export async function listDir(path: string): Promise<FileEntry[]> {
  const fn = await getInvoke();
  try {
    const result = await fn("list_dir", { path });
    return Array.isArray(result) ? result : [];
  } catch {
    return [];
  }
}

export async function writeNote(
  repoPath: string,
  name: string,
  content: string
): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("write_note", { repoPath, name, content });
  } catch (e) {
    return { success: false, output: String(e), mode: "write_note" };
  }
}

export async function saveOutput(
  path: string,
  content: string
): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("save_output", { path, content });
  } catch (e) {
    return { success: false, output: String(e), mode: "save" };
  }
}

export async function getHomeDir(): Promise<string> {
  const fn = await getInvoke();
  try {
    const r = await fn("get_home_dir");
    return r.output || "";
  } catch {
    return "";
  }
}

export async function writeFile(
  path: string,
  content: string
): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("write_file", { path, content });
  } catch (e) {
    return { success: false, output: String(e), mode: "write" };
  }
}

export async function pathExists(path: string): Promise<boolean> {
  const fn = await getInvoke();
  try {
    return await fn("path_exists", { path });
  } catch {
    return false;
  }
}

export async function reportFeedback(
  repoPath: string,
  mode: string,
  helpful: boolean,
  note: string = ""
): Promise<TempoResult> {
  const fn = await getInvoke();
  try {
    return await fn("report_feedback", { repoPath, mode, helpful, note });
  } catch (e) {
    return { success: false, output: String(e), mode: "feedback" };
  }
}
