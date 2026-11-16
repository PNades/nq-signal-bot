"use client";
// Animated Tabs: 21st.dev id 24930 (educalvolpz), code read 2026-11-15: no network calls, no remote assets.
// CreamFlow changes: the indicator in cream/gold tokens, the pill on the raised surface, `animated` off = Calm;
// (2026-11-15) an optional small `badge` per tab (the Performance account tabs: "mffur1 · Rapid"), `ariaLabel` for the
// tab list (default "Tabs") and `tabTitle` (a tab's full accessible name).

import { cn } from "@/lib/utils";
import { motion, useReducedMotion } from "motion/react";
import { type ReactNode, useCallback, useId, useState } from "react";

export interface AnimatedTabsProps {
  activeTab?: string;
  className?: string;
  defaultTab?: string;
  layoutId?: string;
  onChange?: (tabId: string) => void;
  tabs: { id: string; label: string; icon?: ReactNode; badge?: string; title?: string }[];
  variant?: "underline" | "pill" | "segment";
  animated?: boolean;
  ariaLabel?: string;
}

const SPRING = { bounce: 0.05, duration: 0.25, type: "spring" as const };

export default function AnimatedTabs({
  tabs, activeTab: controlledActiveTab, defaultTab, onChange, variant = "underline", layoutId: customLayoutId, className, animated = true,
  ariaLabel = "Tabs",
}: AnimatedTabsProps) {
  const shouldReduceMotion = useReducedMotion() || !animated;
  const generatedId = useId();
  const layoutId = customLayoutId ?? `animated-tabs-${generatedId}`;
  const [internalActiveTab, setInternalActiveTab] = useState(defaultTab ?? tabs[0]?.id ?? "");
  const isControlled = controlledActiveTab !== undefined;
  const activeTab = isControlled ? controlledActiveTab : internalActiveTab;

  const handleTabChange = useCallback((tabId: string) => {
    if (!isControlled) setInternalActiveTab(tabId);
    onChange?.(tabId);
  }, [isControlled, onChange]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent, currentIndex: number) => {
    let newIndex = currentIndex;
    if (event.key === "ArrowRight") { event.preventDefault(); newIndex = (currentIndex + 1) % tabs.length; }
    else if (event.key === "ArrowLeft") { event.preventDefault(); newIndex = (currentIndex - 1 + tabs.length) % tabs.length; }
    else if (event.key === "Home") { event.preventDefault(); newIndex = 0; }
    else if (event.key === "End") { event.preventDefault(); newIndex = tabs.length - 1; }
    else return;
    const newTab = tabs[newIndex];
    if (newTab) {
      handleTabChange(newTab.id);
      document.getElementById(`${layoutId}-tab-${newTab.id}`)?.focus();
    }
  }, [tabs, handleTabChange, layoutId]);

  return (
    <div aria-label={ariaLabel} role="tablist" className={cn(
      "relative inline-flex",
      variant === "underline" && "gap-1 border-b border-line-strong/60",
      variant === "pill" && "gap-1 rounded-full bg-bg-3/70 p-1",
      variant === "segment" && "gap-0 rounded-sm bg-bg-3/70 p-0.5",
      className,
    )}>
      {tabs.map((tab, index) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            aria-selected={isActive}
            aria-label={tab.title}
            className={cn(
              "relative z-10 flex cursor-pointer items-center justify-center gap-2 whitespace-nowrap font-medium transition-colors duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cream",
              variant === "underline" && "rounded-t-xs px-3.5 py-2 text-[13.5px]",
              variant === "pill" && "rounded-full px-3.5 py-1.5 text-[13px]",
              variant === "segment" && "flex-1 rounded-xs px-2.5 py-1 font-mono text-[11.5px]",
              isActive ? "text-text-1" : "text-text-2 hover:text-text-1",
            )}
            id={`${layoutId}-tab-${tab.id}`} key={tab.id} onClick={() => handleTabChange(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, index)} role="tab" tabIndex={isActive ? 0 : -1} type="button"
          >
            {isActive && (
              <motion.span
                className={cn(
                  "absolute",
                  variant === "underline" && "right-0 -bottom-px left-0 h-0.5 bg-gold",
                  variant === "pill" && "inset-0 rounded-full border border-gold/30 bg-gold/12",
                  variant === "segment" && "inset-0 rounded-xs border border-line-strong bg-bg-2",
                )}
                layout layoutId={layoutId} style={{ originY: "0px" }}
                transition={shouldReduceMotion ? { duration: 0 } : SPRING}
              />
            )}
            {tab.icon ? <span className="relative z-10">{tab.icon}</span> : null}
            <span className="relative z-10">{tab.label}</span>
            {tab.badge ? (
              <span aria-hidden="true" className={cn("relative z-10 rounded-full border px-1.5 py-px font-mono text-[10.5px] font-medium leading-4",
                isActive ? "border-gold/40 text-cream" : "border-line-strong text-text-3")}>{tab.badge}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
