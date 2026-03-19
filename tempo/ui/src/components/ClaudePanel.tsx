import { useState, useEffect } from "react";
import { Shield, X } from "lucide-react";
import { listDir, readFile, writeFile, getHomeDir } from "./tempo";
import { useClaudeSections } from "./useClaudeSections";
import { ClaudePanelSectionTree } from "./ClaudePanelSectionTree";
import { ClaudePanelContentPane } from "./ClaudePanelContentPane";
import type { DirEntry } from "./ClaudePanel.types";

interface Props {
  onClose: () => void;
  workspaces?: string[];
}

export function ClaudePanel({ onClose, workspaces = [] }: Props) {
  const [homeDir, setHomeDir] = useState("");
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(["settings", "global"])
  );
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

  const { sections, buildSections } = useClaudeSections(workspaces);
  const claudeDir = `${homeDir}/.claude`;

  useEffect(() => {
    getHomeDir().then((h) => {
      if (!h) return;
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

  const isEditable = (path: string) => {
    const editableExts = [".json", ".md", ".sh", ".toml", ".yaml", ".yml", ".txt"];
    return editableExts.some((ext) => path.endsWith(ext));
  };

  return (
    <div className="claude-panel">
      <div className="claude-header">
        <Shield size={14} color="var(--accent)" />
        <span style={{ fontWeight: 600, fontSize: 13 }}>Claude Code Configuration</span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)", marginLeft: 4 }}>
          {claudeDir}
        </span>
        <button
          className="btn btn-ghost"
          onClick={onClose}
          style={{ marginLeft: "auto", padding: "2px 6px" }}
        >
          <X size={12} />
        </button>
      </div>
      <div className="claude-body">
        <ClaudePanelSectionTree
          sections={sections}
          expandedSections={expandedSections}
          activeFile={activeFile}
          onToggle={toggleSection}
          onOpenFile={openFile}
          onOpenSubDir={openSubDir}
        />
        <ClaudePanelContentPane
          claudeDir={claudeDir}
          subDirPath={subDirPath}
          subDir={subDir}
          subDirSection={subDirSection}
          activeFile={activeFile}
          activeFileName={activeFileName}
          fileContent={fileContent}
          originalContent={originalContent}
          editing={editing}
          saving={saving}
          saved={saved}
          onClearSubDir={() => setSubDirPath("")}
          onOpenFile={openFile}
          onOpenSubDir={openSubDir}
          onSetEditing={setEditing}
          onSave={saveFile}
          onCancelEdit={() => { setEditing(false); setFileContent(originalContent); }}
          onContentChange={setFileContent}
          isEditable={isEditable}
        />
      </div>
    </div>
  );
}
