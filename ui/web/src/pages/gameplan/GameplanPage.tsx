// Gameplan (SPEC §3.4, §5.6, §9b item 4; design-system/pages/gameplan.md; build stage 5): the day's status, the time
// to the next session mark on the Flip-Disk Matrix, the day timeline (setup windows, news blackouts, last entry,
// flat-by, now), the drop zone (a file is converted and its cost estimated first; the paid parse only after the owner
// approves the printed worst case, rule 2), the parsed gameplan as an editable table (each edit an owner override, so a
// re-parse never loses it; flagged fields marked), the scenarios, the levels with their tags, and Confirm. Only a
// confirmation before 09:30 ET of the gameplan's own day counts for that day's live trading.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Check, FileUp, Loader2, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useAnimated } from "@/effects";
import { ApiError, postFile, postJson } from "@/lib/api";
import { load, peek, prime } from "@/lib/prefetch";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";
import { AutoHeight, FooterPage, PageHeader, Skeleton } from "@/shell/page";
import { pageById } from "@/shell/pages";
import { btn } from "@/components/ui/dialog";
import { FlipDiskDisplay } from "@/components/ui/flip-disk-matrix";
import { OptionPicker, type PickGroup } from "@/components/ui/option-picker";
import type { Entry, Flag, GameplanData, Range, Rules, Status, Uploaded } from "./types";

export const gameplanUrl = (day?: string | null) => `/api/gameplan${day ? `?date=${encodeURIComponent(day)}` : ""}`;
/** the day the page opens on: the address's ?date=, else the remembered one, else today */
const openingDay = () => new URLSearchParams(location.search).get("date") ?? gameplanMemory.day;
export const preloadGameplan = () => load<GameplanData>(gameplanUrl(openingDay()));
/** the day the page shows, kept while the panel is open */
export const gameplanMemory: { day: string | null } = { day: null };

const TL_FROM = 8 * 60, TL_TO = 16 * 60 + 15;          // the day timeline's axis, ET minutes (pages/gameplan.md)
const ET_FMT = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const ET_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

const mins = (hhmm: string) => { const [h, m, s] = hhmm.split(":").map(Number); return h * 60 + m + (s || 0) / 60; };
const fmtP = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2 });
const fmtR = (r: Range | undefined) => (!r ? "—" : r[0] === r[1] ? fmtP(r[0]) : `${fmtP(r[0])}–${fmtP(r[1])}`);

function useNowEt() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const id = window.setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id); }, []);
  const [h, m, s] = ET_FMT.format(now).split(":").map(Number);
  return { day: ET_DAY.format(now), min: h * 60 + m + s / 60 };
}

/** the next session mark after now (recomputed from the clock every second, never decremented; MASTER §1.2) */
function nextMark(rules: Rules, isToday: boolean, nowMin: number): { text: string; label: string } {
  if (!rules.trading_day) return { text: "--:--", label: "not a trading day" };
  if (!isToday) return { text: "--:--", label: "not today" };
  const w = rules.windows;
  const marks: [string, string][] = [[w.rth_open, "to the RTH open"], [w.golden_zone_from, "to the golden-zone setups"],
    [w.all_setups_from, "to all setups"], [w.last_entry, "to the last entry"], [w.flat_by, "to flat-by"]];
  const next = marks.find(([t]) => mins(t) > nowMin);
  if (!next) return { text: "--:--", label: "the session is over" };
  const left = Math.max(0, Math.ceil(mins(next[0]) - nowMin));
  return { text: `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(left % 60).padStart(2, "0")}`, label: `${next[1]} (${next[0]} ET)` };
}

/** "09:12", or "2026-10-07 19:59" when the confirmation came on another day than the gameplan's */
const confAt = (s: Status, day: string) => (s.confirmed_date && s.confirmed_date !== day ? `${s.confirmed_date} ${s.confirmed_at}` : s.confirmed_at ?? "");

function statusLine(s: Status, day: string, today: string): { text: string; tone: "ok" | "warn" | "muted" } {
  const which = day === today ? "Today" : day;
  if (s.state === "none") return { text: day === today ? "No gameplan today: the bot runs without it" : `No gameplan for ${day}`, tone: "muted" };
  if (s.state === "parsed") return { text: `${which}: parsed, not confirmed`, tone: "warn" };
  if (s.state === "changed") return { text: `${which}: edited after the ${confAt(s, day)} confirmation: confirm again`, tone: "warn" };
  return { text: `${which}: confirmed ${confAt(s, day)}${s.counts_for_trading ? "" : " (after 09:30 ET that day: saved for training only)"}`, tone: s.counts_for_trading ? "ok" : "warn" };
}

const DAY_TITLE = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export function GameplanPage() {
  const base = pageById("gameplan");
  const animated = useAnimated();
  // a day from the address (/gameplan?date=YYYY-MM-DD; months 7-12 are refused by the server), else the remembered one
  const [day, setDay] = useState<string | null>(openingDay);
  const [data, setData] = useState<GameplanData | undefined>(() => peek<GameplanData>(gameplanUrl(openingDay())));
  const [err, setErr] = useState<string | null>(null);
  const now = useNowEt();

  const refresh = useCallback((d: string | null) => {
    load<GameplanData>(gameplanUrl(d)).then((x) => { setData(x); setErr(null); }).catch((e) => setErr(e instanceof ApiError ? e.message : String(e)));
  }, []);
  useEffect(() => {
    gameplanMemory.day = day; refresh(day);
    const want = day ? `/gameplan?date=${day}` : "/gameplan";
    if (location.pathname === "/gameplan" && location.pathname + location.search !== want) history.replaceState(history.state, "", want);
  }, [day, refresh]);
  const accept = (d: GameplanData) => { prime(gameplanUrl(d.date === d.today ? null : d.date), d); prime(gameplanUrl(d.date), d); setData(d); };

  if (!data) return <Frame><PageHeader page={base} /><Skeleton className="h-[520px] w-full rounded-md" />{err && <p className="text-[13px] text-crit">{err}</p>}</Frame>;
  const isToday = data.date === now.day;
  const notToday = data.date !== data.today;
  // owner 2026-11-16: another day than today is named in the title and marked as not today
  const page = notToday ? { ...base, title: ["Gameplan for", DAY_TITLE.format(new Date(`${data.date}T12:00:00Z`))] as [string, string],
    subtitle: data.date < data.today ? "Not today: a past day's plan, for checking and training only." : "Not today: a coming day's plan." } : base;
  const st = statusLine(data.status, data.date, data.today);
  const flags = data.gameplan?.flags ?? [];
  const review = flags.filter((f) => f.review !== false);

  return (
    <FooterPage>
      <Frame>
        <PageHeader page={page} right={<DayPicker data={data} onPick={(d) => setDay(d === data.today ? null : d)} />}>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {data.synthetic && <span className="rounded-xs bg-warn px-1.5 py-0.5 font-mono text-[11px] font-semibold text-bg-0">SYNTHETIC DATA</span>}
            {notToday && <span className="rounded-xs border border-warn px-1.5 py-0.5 font-mono text-[11px] font-semibold text-warn" data-not-today>NOT TODAY</span>}
            <span className={cn("inline-flex items-center gap-1.5 text-[14px]", st.tone === "ok" ? "text-ok" : st.tone === "warn" ? "text-warn" : "text-text-2")} data-status-line>
              {st.tone === "ok" ? <Check className="size-4" /> : st.tone === "warn" ? <AlertTriangle className="size-4" /> : null}{st.text}
            </span>
          </div>
        </PageHeader>
        {data.empty && <p className="rounded-md border border-warn/50 bg-bg-1 px-4 py-3 text-[13.5px] text-warn">{data.empty}</p>}
        <div className="grid gap-4 min-[1100px]:grid-cols-[1fr_1fr_1.15fr]" data-kpis>
          <Tile label="Status" hint="Only a gameplan confirmed before 09:30 ET is used for that day's live trading">
            <div className={cn("mt-1 text-[20px] font-medium leading-7", st.tone === "ok" ? "text-ok" : st.tone === "warn" ? "text-warn" : "text-text-1")} data-status-value>
              {data.status.state === "none" ? "No gameplan" : data.status.state === "parsed" ? "Not confirmed" : data.status.state === "changed" ? "Confirm again" : `Confirmed ${confAt(data.status, data.date)}`}
            </div>
            <div className="mt-2.5 text-[12.5px] leading-[18px] text-text-2" data-status-sub>{data.gameplan ? `${data.gameplan.source.file} · ${data.gameplan.zones.length} entries · ${data.edits.length} edit${data.edits.length === 1 ? "" : "s"}` : "Drop the morning's file below"}</div>
          </Tile>
          <Tile label="Flags to review" hint="Review flags need your look; info flags only say something is missing or follows a rule">
            <div className={cn("font-mono text-[28px] leading-8", review.length ? "text-warn" : "text-text-1")} data-review-count>{data.gameplan ? review.length : "—"}</div>
            <div className="mt-1 text-[12.5px] text-text-2">{data.gameplan ? `${flags.length - review.length} info` : "no gameplan"}</div>
          </Tile>
          <Countdown rules={data.rules} isToday={isToday} nowMin={now.min} animated={animated} />
        </div>
        {/* a day switch (owner 2026-11-17), as the Performance account and phase switches: the new day's data is loaded
            before it shows (the old day stays meanwhile), the two crossfade and the height glides (AutoHeight, 250 ms
            ease-out), so nothing flashes blank or jumps; Calm: at once */}
        <AutoHeight>
          <AnimatePresence initial={false} mode="popLayout">
            <motion.div key={data.date} className="flex flex-col gap-5" data-day-content={data.date}
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: animated ? 0.2 : 0, ease: "easeOut" }}>
              <Timeline rules={data.rules} nowMin={isToday ? now.min : null} />
              <Upload data={data} day={data.date} onParsed={accept} />
              {data.gameplan && <Parsed data={data} onChange={accept} />}
            </motion.div>
          </AnimatePresence>
        </AutoHeight>
      </Frame>
    </FooterPage>
  );
}

function Frame({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-6 pb-0 pt-7 min-[1440px]:px-8" data-page="gameplan">{children}</div>;
}

// the three top boxes (Status, Flags to review, Next; owner 2026-11-24, round 3): the same inner spacing and the same
// height: 20 px above and below an 80-px content box (before: Status and Flags 16 px, Next 22 px, its content centred
// lower), so the three labels and the three last lines line up across the row
const TILE_BOX = "rounded-md border border-line bg-bg-1 px-4 py-5";
const TILE_CONTENT = "min-h-20";

function Tile({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <section className={cn(TILE_BOX, "flex flex-col justify-center")} data-hint={hint} data-tile>
      <div className={TILE_CONTENT}>
        <div className="eyebrow">{label}</div>
        <div className="mt-2">{children}</div>
      </div>
    </section>
  );
}

const DAY_SHORT = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const MONTH_NAME = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const dayLabel = (d: string) => DAY_SHORT.format(new Date(`${d}T12:00:00Z`));

/** a day's state as a small chip (the Day picker's rows) */
function StateChip({ st }: { st?: { state: Status["state"]; counts_for_trading?: boolean; review_flags?: number } }) {
  if (!st || st.state === "none") return <span className="rounded-xs border border-line-strong px-1.5 py-px text-[10.5px] text-text-3">none</span>;
  const [txt, cls] = st.state === "parsed" ? ["not confirmed", "border-warn/60 text-warn"]
    : st.state === "changed" ? ["confirm again", "border-warn/60 text-warn"]
    : st.counts_for_trading ? ["confirmed", "border-ok/60 text-ok"] : ["training only", "border-line-strong text-text-2"];
  return <span className={cn("rounded-xs border px-1.5 py-px text-[10.5px]", cls)}>{txt}</span>;
}

/** the Day picker (owner 2026-11-17): the panel's own dropdown, today at the top with its state, then the stored days
 *  grouped by month, newest first (months 7-12 never listed until gate 16: the server leaves them out) */
function DayPicker({ data, onPick }: { data: GameplanData; onPick: (d: string) => void }) {
  const animated = useAnimated();
  const states = data.day_states ?? {};
  const rest = data.days.filter((d) => d !== data.today).sort().reverse();
  const groups: PickGroup[] = [{ label: "Today", options: [{ value: data.today, label: `${dayLabel(data.today)} · today`, note: <StateChip st={states[data.today]} /> }]}];
  for (const d of rest) {
    const m = MONTH_NAME.format(new Date(`${d}T12:00:00Z`));
    if (groups[groups.length - 1].label !== m) groups.push({ label: m, options: [] });
    groups[groups.length - 1].options.push({ value: d, label: dayLabel(d), note: <StateChip st={states[d]} /> });
  }
  return (
    <div className="flex items-center gap-2">
      <span className="eyebrow">Day</span>
      <OptionPicker value={data.date} groups={groups} onChange={onPick} ariaLabel={`Day: ${data.date}`} animated={animated} align="right" width={280}
        dataAttr="data-day-picker" trigger={<span>{data.date === data.today ? `${dayLabel(data.date)} · today` : dayLabel(data.date)}</span>} />
    </div>
  );
}

function Countdown({ rules, isToday, nowMin, animated }: { rules: Rules; isToday: boolean; nowMin: number; animated: boolean }) {
  const m = nextMark(rules, isToday, nowMin);
  return (
    <section className={cn(TILE_BOX, "flex items-center justify-between gap-3")} data-tile data-countdown>
      {/* the label at the top and the note at the bottom of the same 80-px box as the other two tiles */}
      <div className={cn(TILE_CONTENT, "flex flex-col justify-between")}>
        <div className="eyebrow">Next</div>
        <div className="text-[13px] text-text-2">{m.label}</div>
        <div className="font-mono text-[11px] text-text-3">hours : minutes, New York time</div>
      </div>
      <FlipDiskDisplay text={m.text} rows={9} animated={animated} className="w-[170px]" label={`${m.text} ${m.label}`} />
    </section>
  );
}

// ── the day timeline: 08:00–16:15 ET ─────────────────────────────────────────────────────────────────────────────

function Timeline({ rules, nowMin }: { rules: Rules; nowMin: number | null }) {
  const W = 1000, H = 92, x = (m: number) => ((Math.max(TL_FROM, Math.min(TL_TO, m)) - TL_FROM) / (TL_TO - TL_FROM)) * W;
  const w = rules.windows;
  const ticks = [];
  for (let m = TL_FROM; m <= TL_TO; m += 30) ticks.push(m);
  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="The day's timeline" data-timeline>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="eyebrow">The day, New York time</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-text-2">
          <Legend swatch="bg-gold/25">golden-zone setups</Legend><Legend swatch="bg-gold/55">all setups</Legend>
          <Legend swatch="bg-warn/35">news blackout</Legend>
          {rules.early_close && <span className="text-warn">early close {rules.early_close}</span>}
          {!rules.trading_day && <span className="text-warn">not a trading day</span>}
        </div>
      </div>
      {/* the axis is inset from the card's edges (owner 2026-11-24, round 2): every hour label, 08:00 too, sits centred on its
          tick with room to spare; the axis's end (16:15) has a tick and no label (it would crowd 16:00) */}
      <div className="px-5" data-timeline-axis>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-3 block h-[92px] w-full" preserveAspectRatio="none" role="img"
        aria-label={`Setups ${w.golden_zone_from} golden zone, ${w.all_setups_from} all, last entry ${w.last_entry}, flat by ${w.flat_by}`}>
        <defs>
          <pattern id="gp-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="rgba(255,201,61,.12)" /><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(255,201,61,.55)" strokeWidth="2" />
          </pattern>
        </defs>
        <rect x="0" y="22" width={W} height="34" fill="#141414" />
        {rules.trading_day && <>
          <rect x={x(mins(w.golden_zone_from))} y="22" width={x(mins(w.all_setups_from)) - x(mins(w.golden_zone_from))} height="34" fill="rgba(255,224,194,.25)" data-band="golden" />
          <rect x={x(mins(w.all_setups_from))} y="22" width={x(mins(w.last_entry)) - x(mins(w.all_setups_from))} height="34" fill="rgba(255,224,194,.55)" data-band="all" />
          {rules.blackouts.map((b) => (
            <g key={b.at + b.name} data-band="blackout">
              <rect x={x(mins(b.from))} y="18" width={x(mins(b.to)) - x(mins(b.from))} height="42" fill="url(#gp-hatch)" />
              <line x1={x(mins(b.at))} x2={x(mins(b.at))} y1="14" y2="64" stroke="#ffc93d" strokeWidth="1.5" />
            </g>
          ))}
          {[[w.rth_open, "open"], [w.last_entry, "last entry"], [w.flat_by, "flat"]].map(([t, n]) => (
            <line key={n} x1={x(mins(t))} x2={x(mins(t))} y1="16" y2="62" stroke="#eeeeee" strokeWidth="1" strokeDasharray={n === "open" ? "" : "3 3"} />
          ))}
        </>}
        {nowMin != null && nowMin >= TL_FROM && nowMin <= TL_TO && <line x1={x(nowMin)} x2={x(nowMin)} y1="6" y2="70" stroke="#ff9a3c" strokeWidth="2" data-now />}
        {ticks.map((m) => <line key={m} x1={x(m)} x2={x(m)} y1="56" y2={m % 60 === 0 ? 64 : 60} stroke="#484848" strokeWidth="1" />)}
      </svg>
      {/* the labels as HTML (crisp at every width; the SVG stretches) */}
      <div className="relative mt-1 h-4 font-mono text-[11px] text-text-3">
        {ticks.filter((m) => m % 60 === 0).map((m) => (
          <span key={m} className="absolute -translate-x-1/2" style={{ left: `${(x(m) / W) * 100}%` }} data-tick-label>{`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`}</span>
        ))}
      </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[12.5px] text-text-2" data-marks>
        <span>open <b className="font-mono font-normal text-text-1">{w.rth_open}</b></span>
        <span>golden zone from <b className="font-mono font-normal text-text-1">{w.golden_zone_from}</b></span>
        <span>all setups from <b className="font-mono font-normal text-text-1">{w.all_setups_from}</b></span>
        <span>last entry <b className="font-mono font-normal text-text-1">{w.last_entry}</b></span>
        <span>flat by <b className="font-mono font-normal text-text-1">{w.flat_by}</b></span>
        {rules.blackouts.map((b) => (
          <span key={b.at + b.name} className="text-warn" data-blackout>{b.at} {b.name}{b.live_only ? " (live only)" : ""}: no entries {b.from.slice(0, 5)}–{b.to}</span>
        ))}
        {rules.trading_day && !rules.blackouts.length && <span>no news blackout</span>}
      </div>
    </section>
  );
}

function Legend({ swatch, children }: { swatch: string; children: ReactNode }) {
  return <span className="inline-flex items-center gap-1.5"><span className={cn("inline-block size-2.5 rounded-[2px]", swatch)} aria-hidden="true" />{children}</span>;
}

// ── upload: convert and estimate first, the paid parse only after the owner approves ─────────────────────────────

function Upload({ data, day, onParsed }: { data: GameplanData; day: string; onParsed: (d: GameplanData) => void }) {
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState<"upload" | "parse" | null>(null);
  const [up, setUp] = useState<Uploaded | null>(null);
  const [done, setDone] = useState<{ file: string; cost: number; estimate: number | null; day: string } | null>(null);
  const [replace, setReplace] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const accept = data.accept.filter((s) => s !== ".txt");

  const send = async (f: File) => {
    setErr(null); setUp(null); setReplace(false); setDone(null);
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!accept.includes(ext)) { setErr(`${f.name}: a gameplan is a ${accept.join(", ")} file`); return; }
    setBusy("upload");
    try {
      setUp(await postFile<Uploaded>("/api/gameplan/upload", f, { "x-file-name": encodeURIComponent(f.name), "x-gameplan-date": day }));
    } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(null); }
  };
  const parse = async () => {
    if (!up?.upload) return;
    setBusy("parse"); setErr(null); setFailed(false);
    try {
      const d = await postJson<GameplanData>("/api/gameplan/parse", { upload: up.upload, approve_usd: up.worst_usd, replace });
      setUp(null); onParsed(d);
      setDone({ file: d.parsed?.file ?? up.name, cost: d.parsed?.cost_usd ?? 0, estimate: d.parsed?.estimate_usd ?? null, day: d.date });
      notify({ kind: "info", title: `Gameplan parsed: ${d.date}`, text: `${d.parsed?.file} · cost $${(d.parsed?.cost_usd ?? 0).toFixed(2)} · check it, then Confirm`, source: "gameplan" });
    } catch (e) {
      // never retried by itself: the file stays staged, and the button sends it again only on another click
      setErr(e instanceof ApiError ? e.message : String(e)); setFailed(true);
    } finally { setBusy(null); }
  };
  const cancel = async () => { if (up?.upload) await postJson("/api/gameplan/discard", { upload: up.upload }).catch(() => {}); setUp(null); };

  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="Upload the gameplan" data-upload>
      <div className="eyebrow">Gameplan file for {day}</div>
      <div role="button" tabIndex={0} data-dropzone
        onClick={() => input.current?.click()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) void send(f); }}
        className={cn("mt-3 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed px-6 py-7 text-center transition-colors",
          drag ? "border-cream bg-caramel/40" : "border-line-strong bg-bg-inset hover:border-text-3")}>
        {busy === "upload" ? <Loader2 className="size-6 animate-spin text-cream" /> : <FileUp className="size-6 text-cream" />}
        <div className="text-[14px] text-text-1">{busy === "upload" ? "Reading the file and counting its tokens (free)…" : "Drop the gameplan here, or click to choose"}</div>
        <div className="text-[12.5px] text-text-2">{accept.join(", ")} · nothing is parsed or paid until you approve the cost</div>
        <input ref={input} type="file" accept={accept.join(",")} className="hidden" data-file-input
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void send(f); }} />
      </div>
      {err && <p className="mt-3 text-[13px] text-crit" role="alert" data-upload-error>{err}</p>}
      {done && (
        <p className="mt-3 flex flex-wrap items-center gap-x-2 text-[13.5px] text-text-1" data-parse-cost>
          <Check className="size-4 text-ok" />Parsed {done.file} for {done.day}: actual cost <b className="font-mono font-normal">${done.cost.toFixed(2)}</b>
          <span className="text-text-2">{done.estimate != null ? `(estimate up to $${done.estimate.toFixed(2)})` : "(rebuilt from its saved answer, free)"}</span>
        </p>
      )}
      {up?.already && <p className="mt-3 text-[13px] text-text-2" data-already>{up.name} is already parsed (for {up.already}); nothing was sent.</p>}
      {up?.upload && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-line-strong bg-bg-2 px-4 py-3" data-estimate>
          <div className="min-w-0 text-[13.5px]">
            <div className="truncate text-text-1">{up.name} <span className="text-text-2">for {up.date}</span></div>
            <div className="mt-0.5 text-text-2">
              {up.cached ? "Its answer is saved already: rebuilt for free." : <>{(up.input_tokens ?? 0).toLocaleString("en-US")} input tokens · up to <b className="font-mono font-normal text-text-1">${(up.worst_usd ?? 0).toFixed(2)}</b> (limit ${(up.cap_usd ?? data.cap_usd).toFixed(2)} a file)</>}
            </div>
            {up.over_cap && <div className="mt-1 text-warn">Over the per-file limit: parse it with <span className="font-mono">uv run nqbot gameplan &lt;file&gt; --approve-usd {(up.worst_usd ?? 0).toFixed(2)}</span> after your OK.</div>}
            {up.after_open && <div className="mt-1 text-warn" data-after-open>It's after 09:30 ET on {up.date}: this gameplan is saved for training only; it doesn't change that day's trading.</div>}
            {up.replaces && (
              <label className="mt-1.5 flex items-center gap-2 text-warn">
                <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} data-replace /> Replace the gameplan stored for {up.date}
              </label>
            )}
          </div>
          <div className="flex shrink-0 gap-2">
            <button type="button" className={btn.ghost} onClick={() => void cancel()} disabled={busy === "parse"}><X className="size-4" />Cancel</button>
            <button type="button" className={btn.primary} onClick={() => void parse()} data-parse
              disabled={busy === "parse" || up.over_cap || (up.replaces && !replace)}>
              {busy === "parse" ? <><Loader2 className="size-4 animate-spin" />Parsing…</> : up.cached ? "Rebuild (free)" : `${failed ? "Parse again" : "Parse"} · up to $${(up.worst_usd ?? 0).toFixed(2)}`}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

// ── the parsed gameplan ─────────────────────────────────────────────────────────────────────────────────────────

/** an entry flag's sub-field -> the table's column (the server's `entry_col`: "trigger.es_gates" -> es_gates,
 *  "trigger_level" -> trigger) */
function entryCol(sub: string): string {
  if (sub === "es_gates" || sub.startsWith("trigger.es_gates")) return "es_gates";
  if (sub.startsWith("trigger")) return "trigger";
  return sub.split(".")[0];
}

/** the review flags on one entry's column ("zones[E2].zone"), on the entry itself (col ""), or plan-wide (id null) */
function flagsAt(flags: Flag[], id: string | null, col: string): Flag[] {
  return flags.filter((f) => {
    if (f.review === false) return false;
    const m = f.field.match(/^zones\[([^\]]+)\](?:\.(.+))?$/);
    if (id === null) return !m && (f.field === col || f.field.split(",").includes(col));
    return !!m && m[1] === id && (col === "" ? !m[2] : entryCol(m[2] ?? "") === col);
  });
}

/** the context the flag buttons need: the day, and how to show the page's new data */
const FlagCtx = createContext<{ date: string; onChange: (d: GameplanData) => void } | null>(null);

function Parsed({ data, onChange }: { data: GameplanData; onChange: (d: GameplanData) => void }) {
  const g = data.gameplan!;
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const save = useCallback(async (field: string, value: string, entry?: string, extra?: Record<string, unknown>) => {
    const d = await postJson<GameplanData>("/api/gameplan/edit", { date: data.date, field, value, entry, ...extra });
    onChange(d);
  }, [data.date, onChange]);
  const confirm = async () => {
    setBusy(true);
    try {
      const d = await postJson<GameplanData>("/api/gameplan/confirm", { date: data.date, note });
      onChange(d); setNote("");
      notify({ kind: d.status.counts_for_trading ? "info" : "warn", title: `Gameplan ${d.date} confirmed ${d.status.confirmed_at}`,
        text: d.status.counts_for_trading ? "It counts for the day's trading." : "After 09:30 ET: saved for training only.", source: "gameplan" });
    } catch (e) { notify({ kind: "warn", title: "Not confirmed", text: e instanceof ApiError ? e.message : String(e), source: "gameplan" }); }
    finally { setBusy(false); }
  };
  const flags = g.flags ?? [];
  const planFlag = (c: string) => flagsAt(flags, null, c);

  const blocked = !!data.status.before_open && (data.status.review_flags ?? 0) > 0;
  return (
    <FlagCtx.Provider value={{ date: data.date, onChange }}>
      <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="The parsed gameplan" data-parsed>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="eyebrow">Entries · click a value to edit it (each edit is kept apart, so a re-parse never loses it)</div>
          <div className="font-mono text-[11.5px] text-text-3">{g.source.file} · parser v{g.source.parser_version}{g.source.cost_usd != null ? ` · $${g.source.cost_usd.toFixed(2)}` : ""}</div>
        </div>
        <div className="mt-3 grid gap-3 min-[900px]:grid-cols-3" data-plan-row>
          <PlanCell label="HVL" value={g.hvl} flags={planFlag("hvl")} onSave={(v) => save("hvl", v)} />
          <PlanCell label="HVL0" value={g.hvl0} flags={planFlag("hvl0")} onSave={(v) => save("hvl0", v)} />
          <PlanCell label="Regime line" value={g.regime_line} flags={planFlag("regime_line")} onSave={(v) => save("regime_line", v)} />
        </div>
        <div className="mt-4 overflow-x-auto rounded-sm border border-line" data-entries>
          <table className="w-full min-w-[980px] border-collapse text-[13px]">
            <thead className="sticky top-0 bg-bg-2 text-left">
              <tr>{["Entry", "Side", "Zone", "Trigger", "ES gates", "Stop", "Targets", "Invalidation"].map((h, i) => (
                <th key={h} className={cn("eyebrow px-3 py-2 font-normal", i === 0 && "sticky left-0 z-10 bg-bg-2")}>{h}</th>))}</tr>
            </thead>
            <tbody>
              {g.zones.map((z) => <EntryRow key={z.id} z={z} flags={flags} save={save} />)}
            </tbody>
          </table>
        </div>
        {g.zones.some((z) => z.other_conditions?.length) && (
          <details className="mt-3 text-[13px] text-text-2">
            <summary className="cursor-pointer text-text-1">Conditions written on the entries</summary>
            <ul className="mt-2 flex flex-col gap-1">{g.zones.flatMap((z) => (z.other_conditions ?? []).map((c, i) => <li key={z.id + i}><span className="font-mono text-text-3">{z.id}</span> {c}</li>))}</ul>
          </details>
        )}
      </section>

      <div className="grid gap-4 min-[1100px]:grid-cols-[1.4fr_1fr]">
        <Scenarios data={data} save={save} />
        <Levels data={data} />
      </div>

      <FlagsPanel flags={flags} edits={data.edits.length} accepted={data.status.accepted ?? 0} />

      <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="The pre-open chart">
        <div className="eyebrow">Pre-open NQ chart</div>
        <p className="mt-2 text-[13px] text-text-2">The gameplan's zones on today's pre-open chart join at step 22, when the pre-open data path exists.</p>
      </section>

      <section className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line-strong bg-bg-2 p-4" data-confirm-panel>
        <div className="min-w-0 text-[13.5px]">
          <div className="text-text-1">{data.status.state === "confirmed" ? `Confirmed ${confAt(data.status, data.date)}${data.status.confirmed_note ? ` · ${data.status.confirmed_note}` : ""}` : data.status.state === "changed" ? "Edited after the confirmation: confirm again" : "Check the entries and the flags, then confirm"}</div>
          <div className="mt-0.5 text-text-2">Only a gameplan confirmed before 09:30 ET on {data.date} reaches that day's live trading; later, it's saved for training.</div>
          {blocked && <div className="mt-1 text-warn" data-confirm-blocked>{data.status.review_flags} review flag{data.status.review_flags === 1 ? " is" : "s are"} still open: before 09:30 fix each one or mark it OK as parsed.</div>}
          {!data.status.before_open && (data.status.review_flags ?? 0) > 0 && <div className="mt-1 text-text-2">After 09:30 this confirmation is for training only, so open flags are allowed.</div>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" aria-label="Confirmation note"
            className="h-9 w-[220px] rounded-sm border border-line-strong bg-bg-inset px-3 text-[13px] text-text-1 placeholder:text-text-3 focus:border-cream" />
          <button type="button" className={btn.primary} onClick={() => void confirm()} disabled={busy || blocked} data-confirm>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}{data.status.state === "confirmed" ? "Confirm again" : "Confirm gameplan"}
          </button>
        </div>
      </section>
    </FlagCtx.Provider>
  );
}

/** "OK as parsed" for one open review flag (matched by its field and exact text, so a reworded flag comes back) */
function OkButton({ flag }: { flag: Flag }) {
  const ctx = useContext(FlagCtx);
  const [busy, setBusy] = useState(false);
  if (!ctx || flag.review === false) return null;
  return (
    <button type="button" disabled={busy} data-ok-flag
      className="ml-1.5 whitespace-nowrap rounded-xs border border-warn/60 px-1.5 py-px font-mono text-[10.5px] text-warn hover:bg-warn/10 disabled:opacity-50"
      onClick={async (e) => {
        e.stopPropagation(); setBusy(true);
        try { ctx.onChange(await postJson<GameplanData>("/api/gameplan/accept", { date: ctx.date, field: flag.field, flag: flag.reason })); }
        catch (err) { notify({ kind: "warn", title: "Not marked", text: err instanceof ApiError ? err.message : String(err), source: "gameplan" }); }
        finally { setBusy(false); }
      }}>OK as parsed</button>
  );
}

function FlagNote({ flags }: { flags: Flag[] }) {
  if (!flags.length) return null;
  return (
    <div className="mt-1 flex max-w-[44ch] flex-col gap-1 text-[11.5px] leading-4 text-warn" data-flag-note>
      {flags.map((f, i) => <div key={i}>{f.reason}<OkButton flag={f} /></div>)}
    </div>
  );
}

function EditCell({ text, edit, flags, save, mono = true, options }: {
  text: string; edit: string; flags: Flag[]; save: (v: string) => Promise<void>; mono?: boolean; options?: string[];
}) {
  return (
    <td className={cn("px-3 py-2 align-top", flags.length > 0 && "border-l-2 border-l-warn bg-warn/[.06]")} data-cell data-flagged={flags.length > 0 || undefined}>
      <Inline text={text} edit={edit} save={save} mono={mono} options={options} />
      <FlagNote flags={flags} />
    </td>
  );
}

/** a value that turns into an input (or a select) on click: Enter saves, Esc cancels */
function Inline({ text, edit, save, mono = true, options, wide = false, startOpen = false, onDone }: {
  text: string; edit: string; save: (v: string) => Promise<void>; mono?: boolean; options?: string[]; wide?: boolean;
  startOpen?: boolean; onDone?: () => void;   // an add-box: open at once, and say when it closes
}) {
  const animated = useAnimated();
  const [on, setOnState] = useState(startOpen);
  const setOn = (x: boolean) => { setOnState(x); if (!x) onDone?.(); };
  const [v, setV] = useState(edit);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const commit = async (val = v) => {
    setBusy(true); setErr(null);
    try { await save(val); setOn(false); } catch (e) { setErr(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <span className={cn("block", wide && "w-full")} data-inline>
      {on ? (
        options ? (
          <OptionPicker value={v} groups={[{ options: options.map((o) => ({ value: o, label: o })) }]} ariaLabel={`Choose: ${options.join(", ")}`}
            onChange={(x) => { setV(x); void commit(x); }} onClose={() => setOn(false)} autoOpen animated={animated} width={150} trigger={<span>{v}</span>} />
        ) : (
          <input autoFocus value={v} disabled={busy} onChange={(e) => setV(e.target.value)} onBlur={() => !busy && setOn(false)}
            onKeyDown={(e) => { if (e.key === "Enter") void commit(); if (e.key === "Escape") { setV(edit); setOn(false); } }}
            className={cn("h-8 w-full min-w-[120px] rounded-sm border border-line-strong bg-bg-inset px-2 text-[13px] text-text-1 focus:border-cream", mono && "font-mono")} />
        )
      ) : (
        <button type="button" onClick={() => { setV(edit); setOn(true); }} className={cn("text-left hover:text-cream", mono && "font-mono", text === "—" ? "text-text-3" : "text-text-1")}>{text}</button>
      )}
      {err && <span className="mt-1 block text-[11.5px] text-crit">{err}</span>}
    </span>
  );
}

type Save = (field: string, value: string, entry?: string, extra?: Record<string, unknown>) => Promise<void>;

function EntryRow({ z, flags, save }: { z: Entry; flags: Flag[]; save: Save }) {
  const f = (c: string) => flagsAt(flags, z.id, c);
  const gateList = [...(z.trigger?.es_gates ?? []), ...(z.es_gates ?? [])].map((x) => x.as_written).filter(Boolean) as string[];
  const invList = (z.invalidation ?? []).map((x) => x.as_written).filter(Boolean) as string[];
  const targets = (z.targets ?? []).map((t) => (t.low === t.high ? fmtP(t.low) : `${fmtP(t.low)}–${fmtP(t.high)}`));
  const own = f("");
  return (
    <tr className="border-t border-line" data-entry={z.id}>
      <td className={cn("sticky left-0 z-[1] bg-bg-1 px-3 py-2 align-top font-mono text-text-1", own.length > 0 && "border-l-2 border-l-warn")}>
        {z.id}<FlagNote flags={own} />
      </td>
      <EditCell text={z.side.toUpperCase()} edit={z.side} options={["buy", "sell"]} flags={f("side")} save={(v) => save("side", v, z.id)} mono={false} />
      <EditCell text={fmtR(z.zone)} edit={z.zone ? fmtR(z.zone) : ""} flags={f("zone")} save={(v) => save("zone", v, z.id)} />
      <EditCell text={z.trigger?.nq_price ? `${fmtR(z.trigger.nq_price)}${z.trigger.type ? ` (${z.trigger.type})` : ""}` : "—"} edit={z.trigger?.nq_price ? fmtR(z.trigger.nq_price) : ""} flags={f("trigger")} save={(v) => save("trigger", v, z.id)} />
      <EditCell text={gateList.join(" · ") || "—"} edit={gateList.join("; ")} flags={f("es_gates")} save={(v) => save("es_gates", v, z.id)} mono={false} />
      <EditCell text={z.stop != null ? fmtP(z.stop) : "—"} edit={z.stop != null ? String(z.stop) : ""} flags={f("stop")} save={(v) => save("stop", v, z.id)} />
      <EditCell text={targets.join(" → ") || "—"} edit={targets.join(" → ")} flags={f("targets")} save={(v) => save("targets", v, z.id)} />
      <EditCell text={invList.join(" · ") || "—"} edit={invList.join("; ")} flags={f("invalidation")} save={(v) => save("invalidation", v, z.id)} mono={false} />
    </tr>
  );
}

function PlanCell({ label, value, flags, onSave }: { label: string; value: Range; flags: Flag[]; onSave: (v: string) => Promise<void> }) {
  return (
    <table className={cn("w-full rounded-sm border border-line bg-bg-2", flags.length > 0 && "border-l-2 border-l-warn")}><tbody><tr>
      <th className="eyebrow w-[110px] px-3 py-2 text-left font-normal">{label}</th>
      <EditCell text={fmtR(value)} edit={value ? fmtR(value) : ""} flags={flags} save={onSave} />
    </tr></tbody></table>
  );
}

function Scenarios({ data, save }: { data: GameplanData; save: Save }) {
  const sets = data.gameplan!.scenario_sets ?? [];
  const flags = (data.gameplan!.flags ?? []).filter((f) => f.review !== false);
  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="Scenarios" data-scenarios>
      <div className="eyebrow">Scenarios</div>
      {!sets.length && <p className="mt-2 text-[13px] text-text-2">None written.</p>}
      {sets.map((s, i) => (
        <div key={i} className="mt-3">
          <div className="text-[12.5px] text-text-2">{s.frame === "ny_am" ? "NY AM (09:30–12:00)" : s.frame === "session" ? "Session" : "Unspecified"}{s.label_as_written ? ` · ${s.label_as_written}` : ""}</div>
          <ul className="mt-1.5 flex flex-col gap-2">
            {s.scenarios.map((x) => {
              const fl = flags.filter((f) => f.field.includes(`scenarios[${x.id}]`));
              const put = (field: string) => (v: string) => save(`scenario.${field}`, v, undefined, { set: i, scenario: x.id });
              return (
                <li key={x.id} className={cn("grid grid-cols-[44px_56px_84px_1fr] items-start gap-2 text-[13px]", fl.length > 0 && "border-l-2 border-l-warn pl-2")} data-scenario={x.id}>
                  <span className="pt-0.5 font-mono text-text-1">{x.id}</span>
                  <Inline text={x.prob != null ? `${Math.round(x.prob * 100)}%` : "—"} edit={x.prob != null ? String(Math.round(x.prob * 100)) : ""} save={put("prob")} />
                  <span className={cn(x.bias === "up" ? "text-ok" : x.bias === "down" ? "text-pnl-down" : "text-text-2")}>
                    <Inline text={`${x.bias === "up" ? "▲" : x.bias === "down" ? "▼" : "◆"} ${x.bias}`} edit={x.bias} options={["up", "down", "neutral", "mixed"]} save={put("bias")} mono={false} />
                  </span>
                  <span className="min-w-0 text-text-2">
                    <Inline text={x.desc} edit={x.desc} save={put("desc")} mono={false} wide />
                    <FlagNote flags={fl} />
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <PlanConditions data={data} save={save} />
    </section>
  );
}

/** the plan-wide conditions (owner 2026-11-17): each one editable in place (emptied: removed), one more added, all on
 *  the same override layer (kept apart from the parsed file, outlined when flagged, a new confirmation after an edit) */
function PlanConditions({ data, save }: { data: GameplanData; save: Save }) {
  const items = data.gameplan!.other_conditions ?? [];
  const flags = (data.gameplan!.flags ?? []).filter((f) => f.review !== false && f.field.startsWith("other_conditions"));
  const [adding, setAdding] = useState(false);
  return (
    <details className={cn("mt-4 text-[13px] text-text-2", flags.length > 0 && "border-l-2 border-l-warn pl-2")} open={flags.length > 0 || undefined} data-plan-conditions>
      <summary className="cursor-pointer text-text-1">Plan-wide conditions ({items.length}) · click one to edit it; empty it to remove it</summary>
      <FlagNote flags={flags} />
      <ul className="mt-2 flex list-disc flex-col gap-1 pl-5">
        {items.map((c, i) => (
          <li key={`${i}:${c}`} data-plan-condition={i}>
            <Inline text={c} edit={c} mono={false} wide save={(v) => save("other_conditions", v, undefined, { index: i })} />
          </li>
        ))}
      </ul>
      <div className="mt-2 pl-5">
        {adding
          ? <Inline text="" edit="" mono={false} wide startOpen onDone={() => setAdding(false)} save={async (v) => { await save("other_conditions", v, undefined, { index: null }); setAdding(false); }} />
          : <button type="button" className="text-[12.5px] text-cream hover:underline" onClick={() => setAdding(true)} data-add-condition>+ Add a condition</button>}
      </div>
    </details>
  );
}

function Levels({ data }: { data: GameplanData }) {
  const g = data.gameplan!;
  const rows: { price: Range; label: string; tags: string[] }[] = [];
  for (const z of g.zones) {
    if (z.zone) rows.push({ price: z.zone, label: z.id, tags: [z.side, "zone", "gameplan"] });
    if (z.trigger?.nq_price) rows.push({ price: z.trigger.nq_price, label: z.id, tags: [z.side, z.trigger.type ?? "trigger", "gameplan"] });
  }
  for (const [k, v] of [["HVL", g.hvl], ["HVL0", g.hvl0], ["Regime line", g.regime_line]] as const) if (v) rows.push({ price: v, label: k, tags: ["line", "gameplan"] });
  rows.sort((a, b) => b.price![1] - a.price![1]);
  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="Levels" data-levels>
      <div className="eyebrow">Levels, high to low</div>
      <ul className="mt-2 flex flex-col">
        {rows.map((r, i) => (
          <li key={i} className="flex items-center justify-between gap-3 border-t border-line py-1.5 text-[13px] first:border-t-0">
            <span className="font-mono text-text-1">{fmtR(r.price)}</span>
            <span className="flex flex-wrap justify-end gap-1">
              <span className="font-mono text-text-2">{r.label}</span>
              {r.tags.map((t) => <span key={t} className={cn("rounded-xs border px-1.5 py-px font-mono text-[10.5px]",
                t === "buy" ? "border-ok/50 text-ok" : t === "sell" ? "border-pnl-down/50 text-pnl-down" : "border-line-strong text-text-2")}>{t}</span>)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[12px] text-text-3">The bot's own levels join this list at step 22 (tag "bot").</p>
    </section>
  );
}

function FlagsPanel({ flags, edits, accepted }: { flags: Flag[]; edits: number; accepted: number }) {
  const review = flags.filter((f) => f.review !== false), info = flags.filter((f) => f.review === false);
  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="Flags" data-flags>
      <div className="eyebrow">Flags · {review.length} to review · {info.length} info{edits ? ` · ${edits} edit${edits === 1 ? "" : "s"} of yours` : ""}{accepted ? ` · ${accepted} marked OK as parsed` : ""}</div>
      <ul className="mt-2 flex flex-col gap-1.5 text-[13px]">
        {review.map((f, i) => <li key={`r${i}`} className="border-l-2 border-l-warn pl-2.5" data-review-flag><span className="font-mono text-warn">{f.field}</span> <span className="text-text-1">{f.reason}</span> <span className="text-text-3">({f.origin})</span><OkButton flag={f} /></li>)}
        {info.map((f, i) => <li key={`i${i}`} className="pl-3"><span className="font-mono text-text-3">{f.field}</span> <span className="text-text-2">{f.reason}</span></li>)}
        {!flags.length && <li className="text-text-2">No flags.</li>}
      </ul>
    </section>
  );
}
