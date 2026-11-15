// Stage-1 stand-in for a page that a later build stage fills in (docs/design/build_stage_1.md). It shows the page's
// real header and an honest empty state naming the stage, so the shell, dock, transitions and keys can be checked.
import { PageHeader, EmptyState } from "@/shell/page";
import { pageById, type PageId } from "@/shell/pages";

const STAGE: Partial<Record<PageId, { stage: number; what: string }>> = {
  home: { stage: 2, what: "The CreamFlow scene with its six page nodes" },
  accounts: { stage: 2, what: "Account cards, the edit form, presets and Save as preset" },
  settings: { stage: 2, what: "Risk profiles, thresholds, the owner's fixed rules and Effects" },
  performance: { stage: 3, what: "Equity, stats, drawdown, Monte Carlo, the P&L calendar and trades" },
  review: { stage: 4, what: "The setup-card review loop with the main chart and the GEX radial" },
  gameplan: { stage: 5, what: "Upload, parse, check and confirm the morning gameplan" },
};

export function PlaceholderPage({ id }: { id: PageId }) {
  const page = pageById(id);
  const s = STAGE[id];
  return (
    <div className="mx-auto flex max-w-[1180px] flex-col gap-8 px-6 pt-7 min-[1440px]:px-8">
      <PageHeader page={page} />
      <EmptyState word={page.name}
        message={s ? <>Built in stage {s.stage} of step 14: {s.what}. The shell, keys and the kill/flat controls already work here.</> : "Coming in a later step."} />
    </div>
  );
}
