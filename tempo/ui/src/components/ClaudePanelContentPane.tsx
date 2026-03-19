import { Save, Folder, FileText } from "lucide-react";
import type { DirEntry } from "./ClaudePanel.types";

interface Props {
  claudeDir: string;
  subDirPath: string;
  subDir: DirEntry[];
  subDirSection: string;
  activeFile: string | null;
  activeFileName: string;
  fileContent: string;
  originalContent: string;
  editing: boolean;
  saving: boolean;
  saved: boolean;
  onClearSubDir: () => void;
  onOpenFile: (path: string, name: string) => void;
  onOpenSubDir: (path: string, sectionId: string) => void;
  onSetEditing: (v: boolean) => void;
  onSave: () => void;
  onCancelEdit: () => void;
  onContentChange: (v: string) => void;
  isEditable: (path: string) => boolean;
}

export function ClaudePanelContentPane({
  claudeDir,
  subDirPath,
  subDir,
  subDirSection,
  activeFile,
  activeFileName,
  fileContent,
  originalContent,
  editing,
  saving,
  saved,
  onClearSubDir,
  onOpenFile,
  onOpenSubDir,
  onSetEditing,
  onSave,
  onCancelEdit,
  onContentChange,
  isEditable,
}: Props) {
  const isDirty = fileContent !== originalContent;

  return (
    <div className="claude-content">
      {subDirPath && !activeFile ? (
        <div>
          <div className="claude-content-head">
            <button
              className="btn btn-ghost"
              onClick={onClearSubDir}
              style={{ padding: "2px 8px", fontSize: 10 }}
            >
              Back
            </button>
            <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
              {subDirPath.replace(claudeDir, "~/.claude")}
            </span>
          </div>
          <div style={{ padding: 8 }}>
            {subDir.map((e) => (
              <button
                key={e.path}
                className="claude-file-btn"
                onClick={() =>
                  e.is_dir
                    ? onOpenSubDir(e.path, subDirSection)
                    : onOpenFile(e.path, e.name)
                }
                style={{ padding: "4px 8px" }}
              >
                {e.is_dir ? <Folder size={10} color="var(--accent)" /> : <FileText size={10} />}
                <span className="claude-file-name">{e.name}</span>
                {!e.is_dir && (
                  <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-tertiary)" }}>
                    {(e.size / 1024).toFixed(1)}K
                  </span>
                )}
              </button>
            ))}
            {subDir.length === 0 && (
              <div style={{ color: "var(--text-tertiary)", fontSize: 11, padding: 16 }}>
                Empty directory
              </div>
            )}
          </div>
        </div>
      ) : activeFile ? (
        <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
          <div className="claude-content-head">
            <span style={{ fontWeight: 500, fontSize: 12, color: "var(--text-primary)" }}>
              {activeFileName}
            </span>
            <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginLeft: 6 }}>
              {activeFile.replace(claudeDir, "~/.claude")}
            </span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
              {isEditable(activeFile) && !editing && (
                <button
                  className="btn btn-ghost"
                  onClick={() => onSetEditing(true)}
                  style={{ padding: "2px 8px", fontSize: 10 }}
                >
                  Edit
                </button>
              )}
              {editing && isDirty && (
                <button
                  className="btn"
                  onClick={onSave}
                  disabled={saving}
                  style={{ padding: "2px 8px", fontSize: 10 }}
                >
                  <Save size={10} /> {saving ? "Saving..." : saved ? "Saved!" : "Save"}
                </button>
              )}
              {editing && (
                <button
                  className="btn btn-ghost"
                  onClick={onCancelEdit}
                  style={{ padding: "2px 8px", fontSize: 10 }}
                >
                  Cancel
                </button>
              )}
              {saved && (
                <span style={{ fontSize: 10, color: "var(--success)", lineHeight: "24px" }}>
                  Saved
                </span>
              )}
            </div>
          </div>
          {editing ? (
            <textarea
              className="claude-editor"
              value={fileContent}
              onChange={(e) => onContentChange(e.target.value)}
              spellCheck={false}
            />
          ) : (
            <pre className="claude-viewer">{fileContent}</pre>
          )}
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: "100%",
            color: "var(--text-tertiary)",
            fontSize: 12,
          }}
        >
          Select a file from the left to view or edit
        </div>
      )}
    </div>
  );
}
