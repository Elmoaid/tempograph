import { useEffect, useRef, useState } from "react";
import { FolderOpen, Plus, X, RefreshCw } from "lucide-react";

interface WorkspaceTabsProps {
  workspaces: string[];
  activeIdx: number;
  loading: boolean;
  onSelect: (i: number) => void;
  onRemove: (i: number) => void;
  onAdd: (path: string) => void;
}

function folderName(p: string): string {
  return p.split("/").filter(Boolean).pop() || p;
}

export function WorkspaceTabs({ workspaces, activeIdx, loading, onSelect, onRemove, onAdd }: WorkspaceTabsProps) {
  const [addingWs, setAddingWs] = useState(false);
  const [newWsPath, setNewWsPath] = useState("");
  const addInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (addingWs) addInputRef.current?.focus();
  }, [addingWs]);

  const handleAdd = () => {
    if (newWsPath.trim()) {
      onAdd(newWsPath.trim());
      setNewWsPath("");
      setAddingWs(false);
    }
  };

  return (
    <div className="ws-strip">
      {workspaces.map((w, i) => (
        <button
          key={w}
          className={`ws-tab ${i === activeIdx ? "active" : ""}`}
          onClick={() => onSelect(i)}
          title={w}
        >
          <FolderOpen size={12} />
          <span className="ws-tab-name">{folderName(w)}</span>
          {i === activeIdx && loading && <RefreshCw size={10} className="spin" />}
          <span className="ws-tab-close" onClick={(e) => { e.stopPropagation(); onRemove(i); }}>
            <X size={10} />
          </span>
        </button>
      ))}
      {addingWs ? (
        <div className="ws-add-input">
          <input
            ref={addInputRef}
            className="input"
            placeholder="/path/to/repo"
            value={newWsPath}
            onChange={(e) => setNewWsPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAdd();
              if (e.key === "Escape") { setAddingWs(false); setNewWsPath(""); }
            }}
            onBlur={() => { if (!newWsPath.trim()) setAddingWs(false); }}
            style={{ width: 250, fontSize: 11, padding: "3px 8px" }}
          />
        </div>
      ) : (
        <button className="ws-tab ws-add" onClick={() => setAddingWs(true)} title="Add workspace">
          <Plus size={12} />
        </button>
      )}
    </div>
  );
}
