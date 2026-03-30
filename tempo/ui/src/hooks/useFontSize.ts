import { useState, useCallback, useEffect } from "react";

const FONT_SIZE_MIN = 9;
const FONT_SIZE_MAX = 16;
const FONT_SIZE_DEFAULT = 11;
const FONT_SIZE_KEY = "tempo_output_font_size";

export function useFontSize() {
  const [fontSize, setFontSize] = useState<number>(() => {
    const saved = parseInt(localStorage.getItem(FONT_SIZE_KEY) || "", 10);
    return saved >= FONT_SIZE_MIN && saved <= FONT_SIZE_MAX ? saved : FONT_SIZE_DEFAULT;
  });

  const changeFontSize = useCallback((delta: number) => {
    setFontSize(prev => {
      const next = Math.max(FONT_SIZE_MIN, Math.min(FONT_SIZE_MAX, prev + delta));
      localStorage.setItem(FONT_SIZE_KEY, String(next));
      return next;
    });
  }, []);

  const resetFontSize = useCallback(() => {
    setFontSize(FONT_SIZE_DEFAULT);
    localStorage.setItem(FONT_SIZE_KEY, String(FONT_SIZE_DEFAULT));
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!e.metaKey && !e.ctrlKey) return;
      if (e.key === "=" || e.key === "+") { e.preventDefault(); changeFontSize(1); }
      else if (e.key === "-") { e.preventDefault(); changeFontSize(-1); }
      else if (e.key === "0") { e.preventDefault(); resetFontSize(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [changeFontSize, resetFontSize]);

  return {
    fontSize,
    changeFontSize,
    resetFontSize,
    fontSizeMin: FONT_SIZE_MIN,
    fontSizeMax: FONT_SIZE_MAX,
  };
}
