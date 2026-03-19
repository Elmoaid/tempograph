import type { LucideIcon } from "lucide-react";

export interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: string | null;
}

export interface SectionItem {
  name: string;
  path: string;
  editable: boolean;
  isDir?: boolean;
}

export interface Section {
  id: string;
  label: string;
  icon: LucideIcon;
  items: SectionItem[];
}
