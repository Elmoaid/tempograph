import { useState } from "react";
import { GitBranch, Folder, ChevronRight, PenLine, FileText, Layers } from "lucide-react";
import { writeNote } from "./tempo";
import { useAmbientStatus } from "../hooks/useAmbientStatus";
import type { WorkspaceData, DirEntry, NoteEntry } from "./workspaceTypes";

interface FeedbackSummary {
  total: number;
  helpful: number;
  recentNotes: { mode: string; helpful: boolean; note: string; ts: string }[];
  byMode: Record<string, { total: number; helpful: number }>;
}

function parseFeedback(telemetry: string): FeedbackSummary | null {
  const section = telemetry.split("=== feedback.jsonl")[1];
  if (!section) return null;
  const lines = section.split("\n").filter(l => l.trim().startsWith("{"));
  if (lines.length === 0) return null;
  const entries = lines.map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  const helpful = entries.filter((e: { helpful: boolean }) => e.helpful).length;
  const byMode: Record<string, { total: number; helpful: number }> = {};
  entries.forEach((e: { mode: string; helpful: boolean; note?: string; ts?: string }) => {
    if (!byMode[e.mode]) byMode[e.mode] = { total: 0, helpful: 0 };
    byMode[e.mode].total++;
    if (e.helpful) byMode[e.mode].helpful++;
  });
  const recentNotes = entries.filter((e: { note?: string }) => e.note).slice(-5).map((e: { mode: string; helpful: boolean; note: string; ts: string }) => ({
    mode: e.mode, helpful: e.helpful, note: e.note, ts: e.ts,
  }));
  return { total: entries.length, helpful, byMode, recentNotes };
}

interface InfoPanelProps {
  repoPath: string;
  ws: WorkspaceData;
  fileBrowserPath: string;
  fileBrowserEntries: DirEntry[];
  fileViewContent: string | null;
  fileViewName: string;
  noteContent: string | null;
  noteName: string;
  onOpenNote: (path: string, name: string) => Promise<void>;
  onNoteBack: () => void;
  onFileViewBack: () => void;
  onBrowseTo: (path: string) => Promise<void>;
  onViewFile: (path: string, name: string) => Promise<void>;
  onNoteCreated: () => Promise<void>;
}

export function InfoPanel({
  repoPath, ws, fileBrowserPath, fileBrowserEntries, fileViewContent, fileViewName,
  noteContent, noteName, onOpenNote, onNoteBack, onFileViewBack, onBrowseTo, onViewFile, onNoteCreated,
}: InfoPanelProps) {
  const [creatingNote, setCreatingNote] = useState(false);
  const [newNoteName, setNewNoteName] = useState("");
  const [newNoteContent, setNewNoteContent] = useState("");
  const { status: ambientStatus, generate: generateAmbient } = useAmbientStatus(repoPath);

  const handleCreateNote = async () => {
    if (!newNoteName.trim() || !repoPath) return;
    const fname = newNoteName.endsWith(".md") ? newNoteName : `${newNoteName}.md`;
    await writeNote(repoPath, fname, newNoteContent);
    setCreatingNote(false);
    setNewNoteName("");
    setNewNoteContent("");
    await onNoteCreated();
  };

  const fb = parseFeedback(ws.telemetry);
  const rate = fb && fb.total > 0 ? Math.round((fb.helpful / fb.total) * 100) : 0;
  const mdNotes = ws.notes.filter((n: NoteEntry) => n.name.endsWith(".md"));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="cell" style={{ flex: "0 0 auto" }}>
        <div className="cell-head"><GitBranch size={11} /> Git</div>
        <div className="cell-body">
          {ws.git ? (
            <pre className="output" style={{ maxHeight: 130, fontSize: 10 }}>{ws.git}</pre>
          ) : <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No git data</div>}
        </div>
      </div>

      <div className="cell" style={{ flex: "0 0 auto" }}>
        <div className="cell-head"><Layers size={11} /> Ambient Context</div>
        <div className="cell-body" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
          {ambientStatus.exists ? (
            <span style={{ color: "var(--success)" }}>
              ready{ambientStatus.timestamp ? ` · ${ambientStatus.timestamp}` : ""}
            </span>
          ) : (
            <>
              <span style={{ color: "var(--text-tertiary)" }}>not generated</span>
              <button
                className="btn btn-ghost"
                onClick={generateAmbient}
                disabled={ambientStatus.generating}
                style={{ padding: "2px 8px", fontSize: 10 }}
              >
                {ambientStatus.generating ? "Generating…" : "Generate"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="cell" style={{ flex: "0 0 auto" }}>
        <div className="cell-head">
          <Folder size={11} /> Files
          <span style={{ marginLeft: 6, fontSize: 9, color: "var(--text-tertiary)", fontWeight: 400 }}>
            {fileBrowserPath.replace(repoPath, ".") || "."}
          </span>
        </div>
        <div className="cell-body">
          {fileViewContent !== null ? (
            <div>
              <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                <button className="btn btn-ghost" onClick={onFileViewBack} style={{ padding: "2px 8px", fontSize: 10 }}>Back</button>
                <span style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: "24px" }}>{fileViewName}</span>
              </div>
              <pre className="output" style={{ maxHeight: 200, fontSize: 10 }}>{fileViewContent}</pre>
            </div>
          ) : (
            <div style={{ maxHeight: 160, overflowY: "auto" }}>
              {fileBrowserPath !== repoPath && (
                <div className="file-row" onClick={() => onBrowseTo(fileBrowserPath.split("/").slice(0, -1).join("/"))}>
                  <ChevronRight size={10} style={{ transform: "rotate(180deg)" }} />
                  <span style={{ color: "var(--text-tertiary)" }}>..</span>
                </div>
              )}
              {fileBrowserEntries.map((e) => (
                <div
                  key={e.path}
                  className="file-row"
                  onClick={() => e.is_dir ? onBrowseTo(e.path) : onViewFile(e.path, e.name)}
                >
                  {e.is_dir ? <Folder size={11} color="var(--accent)" /> : <FileText size={11} />}
                  <span className={e.is_dir ? "file-row-dir" : ""}>{e.name}</span>
                  {!e.is_dir && <span style={{ marginLeft: "auto", fontSize: 9, color: "var(--text-tertiary)" }}>{(e.size / 1024).toFixed(1)}K</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="cell" style={{ flex: "0 0 auto" }}>
        <div className="cell-head">
          Notes ({mdNotes.length})
          <button className="btn btn-ghost" onClick={() => setCreatingNote(!creatingNote)} style={{ marginLeft: "auto", padding: "1px 6px", fontSize: 10 }}>
            <PenLine size={10} /> New
          </button>
        </div>
        <div className="cell-body">
          {creatingNote ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <input className="input" placeholder="note-name.md" value={newNoteName} onChange={(e) => setNewNoteName(e.target.value)} style={{ fontSize: 11 }} />
              <textarea className="input" placeholder="Note content..." value={newNoteContent} onChange={(e) => setNewNoteContent(e.target.value)} rows={4} style={{ fontSize: 11, resize: "vertical", fontFamily: "var(--font-mono)" }} />
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn" onClick={handleCreateNote} style={{ padding: "3px 10px", fontSize: 10 }}>Save Note</button>
                <button className="btn btn-ghost" onClick={() => setCreatingNote(false)} style={{ padding: "3px 10px", fontSize: 10 }}>Cancel</button>
              </div>
            </div>
          ) : noteContent !== null ? (
            <div>
              <button className="btn btn-ghost" onClick={onNoteBack} style={{ marginBottom: 6, padding: "2px 8px", fontSize: 10 }}>Back</button>
              <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4, color: "var(--text-primary)" }}>{noteName}</div>
              <pre className="output" style={{ maxHeight: 150 }}>{noteContent}</pre>
            </div>
          ) : mdNotes.length > 0 ? (
            mdNotes.map((n: NoteEntry) => (
              <div key={n.path} className="note-item" onClick={() => onOpenNote(n.path, n.name)}>
                <div className="note-item-title">{n.name.replace(".md", "")}</div>
                <div className="note-item-meta">{(n.size / 1024).toFixed(1)} KB</div>
              </div>
            ))
          ) : (
            <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No notes — click "New" to create one</div>
          )}
        </div>
      </div>

      {fb && (
        <div className="cell" style={{ flex: "0 0 auto" }}>
          <div className="cell-head">Agent Feedback ({fb.total})</div>
          <div className="cell-body">
            <div style={{ display: "flex", gap: 12, marginBottom: 8, fontSize: 11 }}>
              <span style={{ color: rate >= 70 ? "var(--success)" : rate >= 40 ? "var(--warning)" : "var(--error)" }}>
                {rate}% helpful
              </span>
              <span style={{ color: "var(--text-tertiary)" }}>{fb.helpful}/{fb.total} reports</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
              {Object.entries(fb.byMode).sort((a, b) => b[1].total - a[1].total).slice(0, 6).map(([mode, data]) => (
                <span key={mode} style={{
                  fontSize: 9, padding: "2px 6px", borderRadius: 4,
                  background: "var(--bg-active)",
                  color: data.helpful / data.total >= 0.7 ? "var(--success)" : "var(--text-secondary)"
                }}>
                  {mode} {Math.round((data.helpful / data.total) * 100)}%
                </span>
              ))}
            </div>
            {fb.recentNotes.length > 0 && (
              <div style={{ fontSize: 10, color: "var(--text-secondary)" }}>
                {fb.recentNotes.map((n, i) => (
                  <div key={i} style={{ marginBottom: 3, display: "flex", gap: 4 }}>
                    <span style={{ color: n.helpful ? "var(--success)" : "var(--error)", flexShrink: 0 }}>{n.helpful ? "+" : "−"}</span>
                    <span style={{ color: "var(--text-tertiary)", flexShrink: 0 }}>[{n.mode}]</span>
                    <span>{n.note}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="cell" style={{ flex: 1 }}>
        <div className="cell-head">Learning &amp; Tokens</div>
        <div className="cell-body">
          {ws.learning && (
            <pre className="output" style={{ maxHeight: 100, marginBottom: 8, fontSize: 10 }}>{ws.learning.output}</pre>
          )}
          {ws.tokens && (
            <pre className="output" style={{ maxHeight: 100, fontSize: 10 }}>{ws.tokens.output}</pre>
          )}
          {!ws.learning && !ws.tokens && <div style={{ color: "var(--text-tertiary)", fontSize: 11 }}>No data</div>}
        </div>
      </div>
    </div>
  );
}
