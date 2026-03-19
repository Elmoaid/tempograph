import { ChevronRight, ChevronDown, Folder, FileText } from "lucide-react";
import type { Section } from "./ClaudePanel.types";

interface Props {
  sections: Section[];
  expandedSections: Set<string>;
  activeFile: string | null;
  onToggle: (id: string) => void;
  onOpenFile: (path: string, name: string) => void;
  onOpenSubDir: (path: string, sectionId: string) => void;
}

export function ClaudePanelSectionTree({
  sections,
  expandedSections,
  activeFile,
  onToggle,
  onOpenFile,
  onOpenSubDir,
}: Props) {
  return (
    <div className="claude-tree">
      {sections.map((sec) => (
        <div key={sec.id}>
          <button className="claude-sec-head" onClick={() => onToggle(sec.id)}>
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
                  onClick={() =>
                    item.isDir
                      ? onOpenSubDir(item.path, sec.id)
                      : onOpenFile(item.path, item.name)
                  }
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
  );
}
