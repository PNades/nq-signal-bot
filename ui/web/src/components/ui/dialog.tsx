// Dialog: the shadcn / originui Dialog pattern (21st.dev @originui/components/dialog, coverage.md "save-as-preset dialog")
// on @radix-ui/react-dialog (already in the tree; its scroll lock carries the panel's style nonce): focus trapped, Esc
// closes (unless `busy`), primary action on the right (MASTER §10 Dialogs). MASTER §6 level 4: bg-3, line-strong, deep
// shadow, dark blurred backdrop. Motion: a short fade/rise in Full, instant in Calm.
import type { ReactNode } from "react";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Dialog({ open, onOpenChange, title, titleItalic, description, children, footer, busy = false, wide = false }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: ReactNode; titleItalic?: ReactNode; description?: ReactNode; children?: ReactNode;
  footer?: ReactNode; busy?: boolean; wide?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={(v) => { if (!busy) onOpenChange(v); }}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[50] bg-black/60 backdrop-blur-[8px]" />
        <D.Content onEscapeKeyDown={(e) => { if (busy) e.preventDefault(); }}
          className={cn("fixed left-1/2 top-1/2 z-[50] grid max-h-[calc(100vh-48px)] w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto rounded-lg border border-line-strong bg-bg-3 p-6 shadow-[0_24px_64px_rgba(0,0,0,.7)]",
            wide && "w-[min(94vw,640px)]")}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <D.Title className="m-0 font-display text-[26px] font-normal leading-[30px] text-text-1">{title}{titleItalic && <> <span className="italic text-cream">{titleItalic}</span></>}</D.Title>
              {description ? <D.Description className="m-0 mt-1.5 text-[13.5px] leading-5 text-text-2">{description}</D.Description> : <D.Description className="sr-only">{String(title)}</D.Description>}
            </div>
            <D.Close disabled={busy} aria-label="Close" className="grid size-8 shrink-0 place-items-center rounded-sm text-text-2 hover:bg-bg-2 hover:text-text-1"><X className="size-4" aria-hidden="true" /></D.Close>
          </div>
          {children}
          {footer && <div className="flex flex-wrap items-center justify-end gap-2 pt-1">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export const btn = {
  primary: "inline-flex h-9 items-center justify-center gap-2 rounded-sm bg-cream px-4 text-[13.5px] font-semibold text-[#081a1b] hover:bg-[#fff0e0] disabled:cursor-not-allowed disabled:opacity-45",
  secondary: "inline-flex h-9 items-center justify-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-4 text-[13.5px] text-text-1 hover:border-text-3 disabled:opacity-45",
  ghost: "inline-flex h-9 items-center justify-center gap-2 rounded-sm px-3 text-[13.5px] text-text-2 hover:bg-bg-3 hover:text-text-1",
};
