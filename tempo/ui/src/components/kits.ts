import type { ComponentType } from "react";
import { Compass, Microscope, GitBranch, CheckSquare, Activity } from "lucide-react";

export interface KitInfo {
  id: string;
  label: string;
  icon: ComponentType<{ size?: number }>;
  description: string;
  needsQuery?: boolean;
}

export const BUILTIN_KITS: KitInfo[] = [
  {
    id: "explore",
    label: "Explore",
    icon: Compass,
    description: "Orient to a new codebase — structure overview + complexity hotspots.",
  },
  {
    id: "deep_dive",
    label: "Deep Dive",
    icon: Microscope,
    description: "Deep-dive into a symbol — focused context + blast radius.",
    needsQuery: true,
  },
  {
    id: "change_prep",
    label: "Change Prep",
    icon: GitBranch,
    description: "Prepare for a code change — diff context + focused symbol context.",
    needsQuery: true,
  },
  {
    id: "code_review",
    label: "Code Review",
    icon: CheckSquare,
    description: "Code review workflow — dead code + hotspot risk + symbol focus.",
  },
  {
    id: "health",
    label: "Health Check",
    icon: Activity,
    description: "Codebase health check — complexity hotspots + dead code candidates.",
  },
];
