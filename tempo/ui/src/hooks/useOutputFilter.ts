import { useState, useMemo, useRef, useCallback } from "react";

export function useOutputFilter(modeOutput: string) {
  const [outputFilter, setOutputFilter] = useState("");
  const [filterVisible, setFilterVisible] = useState(false);
  const filterInputRef = useRef<HTMLInputElement>(null);

  const filteredOutput = useMemo(() => {
    if (!outputFilter.trim() || !modeOutput) return modeOutput;
    const q = outputFilter.toLowerCase();
    return modeOutput.split("\n").filter(line => line.toLowerCase().includes(q)).join("\n");
  }, [modeOutput, outputFilter]);

  const filterMatchCount = useMemo(() => {
    if (!outputFilter.trim() || !modeOutput) return null;
    const q = outputFilter.toLowerCase();
    return modeOutput.split("\n").filter(l => l.toLowerCase().includes(q)).length;
  }, [modeOutput, outputFilter]);

  const onFilterToggle = useCallback(() => {
    setFilterVisible(v => !v);
    setTimeout(() => filterInputRef.current?.focus(), 50);
  }, []);

  const onFilterClose = useCallback(() => {
    setFilterVisible(false);
    setOutputFilter("");
  }, []);

  const resetFilter = useCallback(() => {
    setFilterVisible(false);
    setOutputFilter("");
  }, []);

  return {
    outputFilter,
    setOutputFilter,
    filterVisible,
    setFilterVisible,
    filterInputRef,
    filteredOutput,
    filterMatchCount,
    onFilterToggle,
    onFilterClose,
    resetFilter,
  };
}
