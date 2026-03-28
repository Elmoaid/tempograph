import { useState, useRef } from "react";
import { reportFeedback } from "../components/tempo";

export function buildFeedbackKey(activeMode: string, activeKit: string | null): string {
  return activeKit ? `kit:${activeKit}` : activeMode;
}

export interface UseFeedbackResult {
  feedbackMode: string | null;
  feedbackGiven: React.RefObject<Map<string, boolean>>;
  submitFeedback: (helpful: boolean) => Promise<void>;
}

export function useFeedback(
  repoPath: string,
  activeMode: string,
  activeKit: string | null
): UseFeedbackResult {
  const [feedbackMode, setFeedbackMode] = useState<string | null>(null);
  const feedbackGiven = useRef<Map<string, boolean>>(new Map());

  const submitFeedback = async (helpful: boolean) => {
    const feedbackKey = buildFeedbackKey(activeMode, activeKit);
    if (feedbackGiven.current.has(feedbackKey)) return;
    feedbackGiven.current.set(feedbackKey, helpful);
    setFeedbackMode(feedbackKey);
    const mode = activeKit ? "kit" : activeMode;
    await reportFeedback(repoPath, mode, helpful);
  };

  return { feedbackMode, feedbackGiven, submitFeedback };
}
