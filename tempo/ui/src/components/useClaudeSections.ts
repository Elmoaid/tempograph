import { useState, useCallback } from "react";
import {
  Settings, FileText, Server, Zap, Wrench, Puzzle, Clock, BookOpen, Brain,
} from "lucide-react";
import { listDir, readFile } from "./tempo";
import type { Section, SectionItem, DirEntry } from "./ClaudePanel.types";

export function useClaudeSections(workspaces: string[]) {
  const [sections, setSections] = useState<Section[]>([]);

  const buildSections = useCallback(async (home: string) => {
    const cd = `${home}/.claude`;

    const pluginEntries = await listDir(`${cd}/plugins/.install-manifests`);
    const pluginItems: SectionItem[] = pluginEntries
      .filter((e: DirEntry) => e.name.endsWith(".json"))
      .map((e: DirEntry) => ({
        name: e.name.replace("@claude-plugins-official.json", ""),
        path: e.path,
        editable: false,
      }));

    const hookEntries = await listDir(`${cd}/hooks`);
    const hookItems: SectionItem[] = hookEntries
      .filter((e: DirEntry) => !e.is_dir && !e.name.startsWith("."))
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: true }));

    const skillEntries = await listDir(`${cd}/skills`);
    const skillItems: SectionItem[] = skillEntries
      .filter((e: DirEntry) => e.is_dir)
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: false, isDir: true }));

    const taskEntries = await listDir(`${cd}/scheduled-tasks`);
    const taskItems: SectionItem[] = taskEntries
      .filter((e: DirEntry) => e.is_dir)
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: false, isDir: true }));

    const planEntries = await listDir(`${cd}/plans`);
    const planItems: SectionItem[] = planEntries
      .filter((e: DirEntry) => e.name.endsWith(".md"))
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: true }));

    const projEntries = await listDir(`${cd}/projects`);
    const projItems: SectionItem[] = projEntries
      .filter((e: DirEntry) => e.is_dir)
      .map((e: DirEntry) => ({
        name: e.name.replace(/-/g, "/").replace(/^\//, "~"),
        path: e.path,
        editable: false,
        isDir: true,
      }));

    const projClaudeItems: SectionItem[] = [];
    for (const ws of workspaces) {
      const name = ws.split("/").pop() || ws;
      const r1 = await readFile(`${ws}/CLAUDE.md`);
      if (r1.success) {
        projClaudeItems.push({ name: `${name}/CLAUDE.md`, path: `${ws}/CLAUDE.md`, editable: true });
      }
      const r2 = await readFile(`${ws}/.claude.local.md`);
      if (r2.success) {
        projClaudeItems.push({ name: `${name}/.claude.local.md`, path: `${ws}/.claude.local.md`, editable: true });
      }
      const projClaude = await listDir(`${ws}/.claude`);
      for (const e of projClaude) {
        if (!e.is_dir && (e.name.endsWith(".json") || e.name.endsWith(".md"))) {
          projClaudeItems.push({ name: `${name}/.claude/${e.name}`, path: e.path, editable: true });
        }
      }
    }

    setSections([
      {
        id: "settings",
        label: "Settings",
        icon: Settings,
        items: [
          { name: "settings.json", path: `${cd}/settings.json`, editable: true },
          { name: "settings.local.json", path: `${cd}/settings.local.json`, editable: true },
        ],
      },
      {
        id: "global",
        label: "Global CLAUDE.md",
        icon: FileText,
        items: [{ name: "CLAUDE.md", path: `${cd}/CLAUDE.md`, editable: true }],
      },
      ...(projClaudeItems.length > 0
        ? [{ id: "project-claude", label: `Project CLAUDE.md (${projClaudeItems.length})`, icon: FileText, items: projClaudeItems }]
        : []),
      { id: "mcp", label: "MCP Servers", icon: Server, items: [{ name: ".mcp.json", path: `${cd}/.mcp.json`, editable: true }] },
      { id: "hooks", label: `Hooks (${hookItems.length})`, icon: Zap, items: hookItems },
      { id: "skills", label: `Skills (${skillItems.length})`, icon: Wrench, items: skillItems },
      { id: "plugins", label: `Plugins (${pluginItems.length})`, icon: Puzzle, items: pluginItems },
      { id: "scheduled", label: `Scheduled Tasks (${taskItems.length})`, icon: Clock, items: taskItems },
      { id: "plans", label: `Plans (${planItems.length})`, icon: BookOpen, items: planItems },
      { id: "memory", label: `Project Memory (${projItems.length})`, icon: Brain, items: projItems },
    ]);
  }, [workspaces]);

  return { sections, buildSections };
}
