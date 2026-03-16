import { useState, useEffect, useCallback } from "react";
import {
  Settings, FileText, Folder, ChevronRight, ChevronDown,
  Save, Puzzle, Zap, Clock, Brain, BookOpen, Wrench,
  Server, Shield, X,
} from "lucide-react";
import { listDir, readFile, writeFile, getHomeDir } from "./tempo";

interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified: string | null;
}

interface Section {
  id: string;
  label: string;
  icon: typeof Settings;
  items: SectionItem[];
}

interface SectionItem {
  name: string;
  path: string;
  editable: boolean;
  isDir?: boolean;
}

interface Props {
  onClose: () => void;
  workspaces?: string[];
}

export function ClaudePanel({ onClose, workspaces = [] }: Props) {
  const [homeDir, setHomeDir] = useState("");
  const [sections, setSections] = useState<Section[]>([]);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(["settings", "global"]));
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [activeFileName, setActiveFileName] = useState("");
  const [fileContent, setFileContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [subDir, setSubDir] = useState<DirEntry[]>([]);
  const [subDirPath, setSubDirPath] = useState("");
  const [subDirSection, setSubDirSection] = useState("");

  const claudeDir = `${homeDir}/.claude`;

  const buildSections = useCallback(async (home: string) => {
    const cd = `${home}/.claude`;

    // Load plugin list
    const pluginEntries = await listDir(`${cd}/plugins/.install-manifests`);
    const pluginItems: SectionItem[] = pluginEntries
      .filter((e: DirEntry) => e.name.endsWith(".json"))
      .map((e: DirEntry) => ({
        name: e.name.replace("@claude-plugins-official.json", ""),
        path: e.path,
        editable: false,
      }));

    // Load hooks
    const hookEntries = await listDir(`${cd}/hooks`);
    const hookItems: SectionItem[] = hookEntries
      .filter((e: DirEntry) => !e.is_dir && !e.name.startsWith("."))
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: true }));

    // Load skills
    const skillEntries = await listDir(`${cd}/skills`);
    const skillItems: SectionItem[] = skillEntries
      .filter((e: DirEntry) => e.is_dir)
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: false, isDir: true }));

    // Load scheduled tasks
    const taskEntries = await listDir(`${cd}/scheduled-tasks`);
    const taskItems: SectionItem[] = taskEntries
      .filter((e: DirEntry) => e.is_dir)
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: false, isDir: true }));

    // Load plans
    const planEntries = await listDir(`${cd}/plans`);
    const planItems: SectionItem[] = planEntries
      .filter((e: DirEntry) => e.name.endsWith(".md"))
      .map((e: DirEntry) => ({ name: e.name, path: e.path, editable: true }));

    // Load project memory dirs
    const projEntries = await listDir(`${cd}/projects`);
    const projItems: SectionItem[] = projEntries
      .filter((e: DirEntry) => e.is_dir)
      .map((e: DirEntry) => ({ name: e.name.replace(/-/g, "/").replace(/^\//, "~"), path: e.path, editable: false, isDir: true }));

    // Build per-project CLAUDE.md items from workspaces
    const projClaudeItems: SectionItem[] = [];
    for (const ws of workspaces) {
      const name = ws.split("/").pop() || ws;
      // Check for CLAUDE.md in workspace root
      const r1 = await readFile(`${ws}/CLAUDE.md`);
      if (r1.success) {
        projClaudeItems.push({ name: `${name}/CLAUDE.md`, path: `${ws}/CLAUDE.md`, editable: true });
      }
      // Check for .claude.local.md
      const r2 = await readFile(`${ws}/.claude.local.md`);
      if (r2.success) {
        projClaudeItems.push({ name: `${name}/.claude.local.md`, path: `${ws}/.claude.local.md`, editable: true });
      }
      // Check for project-level .claude/ settings
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
        items: [
          { name: "CLAUDE.md", path: `${cd}/CLAUDE.md`, editable: true },
        ],
      },
      ...(projClaudeItems.length > 0 ? [{
        id: "project-claude",
        label: `Project CLAUDE.md (${projClaudeItems.length})`,
        icon: FileText,
        items: projClaudeItems,
      }] : []),
      {
        id: "mcp",
        label: "MCP Servers",
        icon: Server,
        items: [
          { name: ".mcp.json", path: `${cd}/.mcp.json`, editable: true },
        ],
      },
      {
        id: "hooks",
        label: `Hooks (${hookItems.length})`,
        icon: Zap,
        items: hookItems,
      },
      {
        id: "skills",
        label: `Skills (${skillItems.length})`,
        icon: Wrench,
        items: skillItems,
      },
      {
        id: "plugins",
        label: `Plugins (${pluginItems.length})`,
        icon: Puzzle,
        items: pluginItems,
      },
      {
        id: "scheduled",
        label: `Scheduled Tasks (${taskItems.length})`,
        icon: Clock,
        items: taskItems,
      },
      {
        id: "plans",
        label: `Plans (${planItems.length})`,
        icon: BookOpen,
        items: planItems,
      },
      {
        id: "memory",
        label: `Project Memory (${projItems.length})`,
        icon: Brain,
        items: projItems,
      },
    ]);
  }, [workspaces]);

  useEffect(() => {
    getHomeDir().then((h) => {
      if (!h) return; // No home dir available (browser mode)
      setHomeDir(h);
      buildSections(h);
    });
  }, [buildSections]);

  const toggleSection = (id: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openFile = async (path: string, name: string) => {
    const r = await readFile(path);
    setActiveFile(path);
    setActiveFileName(name);
    const content = r.success ? r.output : `Error reading file: ${r.output || "unknown error"}`;
    setFileContent(content);
    setOriginalContent(r.success ? r.output : "");
    setEditing(false);
    setSaved(false);
    setSubDirPath("");
  };

  const openSubDir = async (path: string, sectionId: string) => {
    const entries = await listDir(path);
    setSubDir(entries as DirEntry[]);
    setSubDirPath(path);
    setSubDirSection(sectionId);
    setActiveFile(null);
  };

  const saveFile = async () => {
    if (!activeFile) return;
    setSaving(true);
    await writeFile(activeFile, fileContent);
    setOriginalContent(fileContent);
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const isDirty = fileContent !== originalContent;
  const isEditable = (path: string) => {
    const editableExts = [".json", ".md", ".sh", ".toml", ".yaml", ".yml", ".txt"];
    return editableExts.some((ext) => path.endsWith(ext));
  };

  return (
    <div className="claude-panel">
      {/* Header */}
      <div className="claude-header">
        <Shield size={14} color="var(--accent)" />
        <span style={{ fontWeight: 600, fontSize: 13 }}>Claude Code Configuration</span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)", marginLeft: 4 }}>{claudeDir}</span>
        <button className="btn btn-ghost" onClick={onClose} style={{ marginLeft: "auto", padding: "2px 6px" }}>
          <X size={12} />
        </button>
      </div>

      <div className="claude-body">
        {/* Left: categories */}
        <div className="claude-tree">
          {sections.map((sec) => (
            <div key={sec.id}>
              <button
                className="claude-sec-head"
                onClick={() => toggleSection(sec.id)}
              >
                {expandedSections.has(sec.id) ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                <sec.icon size={12} />
                <span>{sec.label}</span>
              </button>
              {expandedSections.has(sec.id) && (
                <div className="claude-sec-items">
                  {sec.items.map((item) => (
                    <button
                      key={item.path}
                      className={`claude-file-btn ${activeFile === item.path ? "active" : ""}`}
                      onClick={() => item.isDir ? openSubDir(item.path, sec.id) : openFile(item.path, item.name)}
                    >
                      {item.isDir ? <Folder size={10} color="var(--accent)" /> : <FileText size={10} />}
                      <span className="claude-file-name">{item.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Right: file viewer/editor */}
        <div className="claude-content">
          {subDirPath && !activeFile ? (
            <div>
              <div className="claude-content-head">
                <button className="btn btn-ghost" onClick={() => setSubDirPath("")} style={{ padding: "2px 8px", fontSize: 10 }}>Back</button>
                <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                  {subDirPath.replace(claudeDir, "~/.claude")}
                </span>
              </div>
              <div style={{ padding: 8 }}>
                {subDir.map((e) => (
                  <button
                    key={e.path}
                    className="claude-file-btn"
                    onClick={() => e.is_dir ? openSubDir(e.path, subDirSection) : openFile(e.path, e.name)}
                    style={{ padding: "4px 8px" }}
                  >
                    {e.is_dir ? <Folder size={10} color="var(--accent)" /> : <FileText size={10} />}
                    <span className="claude-file-name">{e.name}</span>
                    {!e.is_dir && <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-tertiary)" }}>{(e.size / 1024).toFixed(1)}K</span>}
                  </button>
                ))}
                {subDir.length === 0 && <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16 }}>Empty directory</div>}
              </div>
            </div>
          ) : activeFile ? (
            <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
              <div className="claude-content-head">
                <span style={{ fontWeight: 500, fontSize: 12, color: "var(--text-primary)" }}>{activeFileName}</span>
                <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginLeft: 6 }}>
                  {activeFile.replace(claudeDir, "~/.claude")}
                </span>
                <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                  {isEditable(activeFile) && !editing && (
                    <button className="btn btn-ghost" onClick={() => setEditing(true)} style={{ padding: "2px 8px", fontSize: 10 }}>Edit</button>
                  )}
                  {editing && isDirty && (
                    <button className="btn" onClick={saveFile} disabled={saving} style={{ padding: "2px 8px", fontSize: 10 }}>
                      <Save size={10} /> {saving ? "Saving..." : saved ? "Saved!" : "Save"}
                    </button>
                  )}
                  {editing && (
                    <button className="btn btn-ghost" onClick={() => { setEditing(false); setFileContent(originalContent); }} style={{ padding: "2px 8px", fontSize: 10 }}>Cancel</button>
                  )}
                  {saved && <span style={{ fontSize: 10, color: "var(--success)", lineHeight: "24px" }}>Saved</span>}
                </div>
              </div>
              {editing ? (
                <textarea
                  className="claude-editor"
                  value={fileContent}
                  onChange={(e) => setFileContent(e.target.value)}
                  spellCheck={false}
                />
              ) : (
                <pre className="claude-viewer">{fileContent}</pre>
              )}
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-tertiary)", fontSize: 12 }}>
              Select a file from the left to view or edit
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
