import type { PluginInfo } from "./PluginPanel";
import type { TempoResult } from "../App";

export interface WorkspaceData {
  overview: TempoResult | null;
  quality: TempoResult | null;
  learning: TempoResult | null;
  tokens: TempoResult | null;
  plugins: PluginInfo[];
  notes: NoteEntry[];
  telemetry: string;
  config: Record<string, unknown>;
  git: string;
  loaded: boolean;
}

export interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: string | null;
}

export interface NoteEntry {
  name: string;
  path: string;
  size: number;
  modified: string | null;
}
