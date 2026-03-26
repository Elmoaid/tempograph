import type { RefObject } from "react";
import { ThumbsUp, ThumbsDown } from "lucide-react";
import { formatAge } from "./modes";

interface OutputFooterProps {
  feedbackGiven: RefObject<Map<string, boolean>>;
  activeMode: string;
  runDuration: number | null;
  outputTs: number | null;
  outputLength: number;
  outputLines: number;
  onFeedback: (helpful: boolean) => void;
}

export function OutputFooter({
  feedbackGiven, activeMode, runDuration, outputTs, outputLength, outputLines, onFeedback,
}: OutputFooterProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6 }}>
      <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginRight: 2 }}>Helpful?</span>
      {feedbackGiven.current.has(activeMode) ? (
        <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>
          {feedbackGiven.current.get(activeMode) ? "✓ marked helpful" : "✓ marked unhelpful"}
        </span>
      ) : (
        <>
          <button
            className="btn btn-ghost"
            onClick={() => onFeedback(true)}
            style={{ padding: "1px 6px", fontSize: 9 }}
            title="Helpful"
            aria-label="Mark as helpful"
          >
            <ThumbsUp size={9} aria-hidden="true" />
          </button>
          <button
            className="btn btn-ghost"
            onClick={() => onFeedback(false)}
            style={{ padding: "1px 6px", fontSize: 9 }}
            title="Not helpful"
            aria-label="Mark as not helpful"
          >
            <ThumbsDown size={9} aria-hidden="true" />
          </button>
        </>
      )}
      <span style={{ fontSize: 9, color: "var(--text-tertiary)", marginLeft: "auto", display: "flex", gap: 8 }}>
        {runDuration !== null && (
          <span title="Run duration" style={{ fontFamily: "var(--font-mono)" }}>
            {runDuration < 10 ? runDuration.toFixed(1) : Math.round(runDuration)}s
          </span>
        )}
        {outputTs && <span title="Time since this output was generated">{formatAge(outputTs)}</span>}
        <span title="Output line count">{outputLines.toLocaleString()} lines</span>
        <span>~{Math.round(outputLength / 4).toLocaleString()} tok</span>
      </span>
    </div>
  );
}
