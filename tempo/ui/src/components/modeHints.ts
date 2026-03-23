export const MODE_HINTS: Record<string, string> = {
  focus:        "--query <symbol>  --depth 1-4  --max-tokens N",
  blast:        "--file <path>  --depth 1-3",
  dead:         "--min-confidence 0-1",
  hotspots:     "--exclude <dir>",
  diff:         "git ref, e.g.  HEAD~1..HEAD  or  main..HEAD",
  overview:     "(no args needed)",
  symbols:      "--query <name>",
  dependencies: "--file <path>",
  architecture: "(no args needed)",
  prepare:      "--query <symbol>",
  lookup:       "--query <symbol>",
  stats:        "(no args needed)",
};
