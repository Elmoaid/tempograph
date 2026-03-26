import { useModeRunner } from "./useModeRunner";
import { CommandPalette } from "./CommandPalette";
import { KitBuilder } from "./KitBuilder";
import { ShortcutHelpOverlay } from "./ShortcutHelpOverlay";
import { WhichKeyOverlay } from "./WhichKeyOverlay";
import { MODES } from "./modes";
import { SidebarTabs } from "./SidebarTabs";
import { OutputPanel } from "./OutputPanel";

interface Props {
  repoPath: string;
  excludeDirs?: string[];
}

export function ModeRunner({ repoPath, excludeDirs }: Props) {
  const vm = useModeRunner(repoPath, excludeDirs);

  return (
    <>
      {vm.paletteOpen && (
        <CommandPalette
          modes={MODES}
          onSelect={(mode, args) => {
            vm.switchMode(mode);
            if (args) vm.setModeArgs(args);
            vm.setSidebarTab("modes");
            setTimeout(() => vm.argsInputRef.current?.focus(), 50);
          }}
          onClose={() => vm.setPaletteOpen(false)}
        />
      )}
      {vm.kitBuilderOpen && (
        <KitBuilder
          repoPath={repoPath}
          onSave={(kitId) => { vm.setKitBuilderOpen(false); vm.loadCustomKits(); setTimeout(() => vm.switchKit(kitId), 100); }}
          onClose={() => vm.setKitBuilderOpen(false)}
        />
      )}
      {vm.showHelp && (
        <ShortcutHelpOverlay onClose={() => vm.setHelpOpen(false)} />
      )}
      {vm.showWhichKey && !vm.showHelp && (
        <WhichKeyOverlay />
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <SidebarTabs
          sidebarTab={vm.sidebarTab}
          onTabChange={vm.setSidebarTab}
          allKits={vm.allKits}
          activeKit={vm.activeKit}
          activeMode={vm.activeMode}
          cachedModes={vm.cachedModes}
          onKitSelect={vm.switchKit}
          onModeSelect={vm.switchMode}
          onCreateKit={() => vm.setKitBuilderOpen(true)}
          onTogglePalette={() => vm.setPaletteOpen(p => !p)}
          onToggleHelp={() => vm.setHelpOpen(p => !p)}
        />

        <OutputPanel
          activeModeInfo={vm.activeModeInfo}
          activeMode={vm.activeKit ? `kit:${vm.activeKit}` : vm.activeMode}
          modeArgs={vm.modeArgs}
          modeRunning={vm.modeRunning}
          modeOutput={vm.modeOutput}
          prevOutput={vm.prevOutput}
          elapsed={vm.elapsed}
          outputTs={vm.outputTs}
          runDuration={vm.runDuration}
          copied={vm.copied}
          saved={vm.saved}
          filterVisible={vm.filterVisible}
          outputFilter={vm.outputFilter}
          filteredOutput={vm.filteredOutput}
          filterMatchCount={vm.filterMatchCount}
          history={vm.history}
          historyOpen={vm.historyOpen}
          feedbackGiven={vm.feedbackGiven}
          feedbackMode={vm.feedbackMode}
          argsInputRef={vm.argsInputRef}
          filterInputRef={vm.filterInputRef}
          searchInputRef={vm.searchInputRef}
          searchText={vm.searchText}
          searchActive={vm.searchActive}
          searchMatchCount={vm.searchMatchCount}
          searchCurrentMatch={vm.searchCurrentMatch}
          onArgsChange={vm.setModeArgs}
          onHistoryOpen={vm.setHistoryOpen}
          onHistorySelect={vm.onHistorySelect}
          onRun={vm.runMode}
          onCancel={vm.cancelMode}
          onCopy={vm.copyOutput}
          onSave={vm.handleSaveOutput}
          onFilterToggle={vm.onFilterToggle}
          onFilterChange={vm.setOutputFilter}
          onFilterClose={vm.onFilterClose}
          onSearchChange={vm.onSearchChange}
          onSearchClose={vm.onSearchClose}
          onSearchNavigate={vm.onSearchNavigate}
          onFeedback={vm.submitFeedback}
          suggestions={vm.suggestions}
          onSuggestionClick={vm.runSuggestion}
        />
      </div>
    </>
  );
}
