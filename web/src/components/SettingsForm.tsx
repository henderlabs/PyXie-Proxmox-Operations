"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { AppSettings } from "@/lib/api";
import { useMe } from "@/lib/useMe";

function getTimezoneOptions(current: string): string[] {
  let zones: string[];
  try {
    // Modern browsers only -- returns the full, accurate IANA tz database,
    // so this never goes stale the way a hardcoded list would.
    zones = Intl.supportedValuesOf("timeZone");
  } catch {
    zones = ["UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Europe/London"];
  }
  if (!zones.includes(current)) zones = [current, ...zones];
  return zones;
}

const INTERVAL_PRESETS = [
  { seconds: 60, label: "Every 1 minute" },
  { seconds: 300, label: "Every 5 minutes" },
  { seconds: 600, label: "Every 10 minutes" },
  { seconds: 900, label: "Every 15 minutes" },
  { seconds: 1800, label: "Every 30 minutes" },
  { seconds: 3600, label: "Every hour" },
  { seconds: 7200, label: "Every 2 hours" },
];

export default function SettingsForm({ initial }: { initial: AppSettings }) {
  const router = useRouter();
  const [form, setForm] = useState(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [customInterval, setCustomInterval] = useState(
    !INTERVAL_PRESETS.some((p) => p.seconds === initial.inventory_refresh_interval_seconds)
  );
  const timezoneOptions = useMemo(() => getTimezoneOptions(initial.timezone), [initial.timezone]);
  const me = useMe();
  const isAdmin = me === undefined || me?.is_admin === true;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Request failed (${res.status})`);
        return;
      }
      setSaved(true);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 max-w-lg">
      <div
        className={`p-3 rounded border ${
          form.pve_mutations_enabled ? "border-bad bg-bad/10" : "border-warn/40 bg-warn/5"
        }`}
      >
        <label className="flex items-center gap-2 text-sm font-medium text-text">
          <input
            type="checkbox"
            disabled={!isAdmin}
            checked={form.pve_mutations_enabled}
            onChange={(e) => setForm({ ...form, pve_mutations_enabled: e.target.checked })}
          />
          Allow PyXie to write to Proxmox VE
        </label>
        <p className="text-[11px] text-muted mt-1.5 normal-case">
          The global switch for every real PVE write this app can make -- migrations, host reboots, package
          updates, backup-job changes, and everything else in Maintenance/Protection. Off by default: with this
          unchecked, every action still fully previews (dry-run, safety checks, a computed plan) but the final
          write is refused, no matter who approves it. This is a separate, independent check from approving an
          individual operation -- both this switch and an approval are required for anything to actually happen
          on real infrastructure. Re-checked fresh on every single write call, so switching it off takes effect
          immediately, even mid-operation, no restart needed either way.
        </p>
        {form.pve_mutations_enabled && (
          <p className="text-[11px] text-bad mt-1.5 normal-case font-medium">
            Currently ON -- approved operations will make real changes to real infrastructure.
          </p>
        )}
      </div>
      <div className="p-3 rounded border border-border">
        <label className="flex items-center gap-2 text-sm font-medium text-text">
          <input
            type="checkbox"
            disabled={!isAdmin}
            checked={form.console_enabled}
            onChange={(e) => setForm({ ...form, console_enabled: e.target.checked })}
          />
          Allow the embedded VM console
        </label>
        <p className="text-[11px] text-muted mt-1.5 normal-case">
          Lets admins open a guest's screen inside PyXie (Console tab). It uses the separate
          &quot;console&quot; credential and gives full keyboard and mouse control of the guest, so it is off by
          default. Checked on every connection and every minute while one is open; switching it off closes open
          consoles. Open in PVE keeps working either way.
        </p>
      </div>
      <div className="p-3 rounded border border-border space-y-2">
        <label className="flex items-center gap-2 text-sm font-medium text-text">
          <input
            type="checkbox"
            disabled={!isAdmin}
            checked={form.shell_enabled}
            onChange={(e) => setForm({ ...form, shell_enabled: e.target.checked })}
          />
          Allow the host shell
        </label>
        <p className="text-[11px] text-muted normal-case">
          Lets admins open a terminal on a Proxmox host inside PyXie (the Shell tab on a host page), like the Shell
          in Proxmox. You sign in at the host&apos;s login prompt with a Linux account (PyXie stores no shell password).
          It uses the separate &quot;shell&quot; credential (or the maintenance one) and has no preview or undo, so it is off
          by default. Each person also needs the &quot;host shell&quot; permission, granted on the Users page. Only the open and close are logged, never what is typed. Checked on every
          connection and every minute while one is open; switching it off closes open shells.
        </p>
        <ShellTokenStatus />
        <label className="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            disabled={!isAdmin || !form.shell_enabled}
            checked={form.shell_require_maintenance}
            onChange={(e) => setForm({ ...form, shell_require_maintenance: e.target.checked })}
          />
          Only while the host is in maintenance mode
        </label>
        <label className="flex items-center gap-2 text-sm text-text">
          Close after
          <input
            type="number" min={5} max={60} className="input w-20"
            disabled={!isAdmin || !form.shell_enabled}
            value={form.shell_idle_minutes}
            onChange={(e) => setForm({ ...form, shell_idle_minutes: Math.max(5, Math.min(60, Number(e.target.value) || 15)) })}
          />
          minutes without typing (5 to 60)
        </label>
      </div>
      <Field label="Sync frequency (inventory, PVE health/status, protection, recommendations -- one shared schedule)">
        <select
          className="input"
          disabled={!isAdmin}
          value={customInterval ? "custom" : form.inventory_refresh_interval_seconds}
          onChange={(e) => {
            if (e.target.value === "custom") {
              setCustomInterval(true);
            } else {
              setCustomInterval(false);
              setForm({ ...form, inventory_refresh_interval_seconds: Number(e.target.value) });
            }
          }}
        >
          {INTERVAL_PRESETS.map((p) => (
            <option key={p.seconds} value={p.seconds}>
              {p.label}
            </option>
          ))}
          <option value="custom">Custom…</option>
        </select>
        {customInterval && (
          <input
            type="number"
            min={30}
            disabled={!isAdmin}
            className="input mt-2"
            placeholder="seconds"
            value={form.inventory_refresh_interval_seconds}
            onChange={(e) => setForm({ ...form, inventory_refresh_interval_seconds: Number(e.target.value) })}
          />
        )}
        <p className="text-[11px] text-muted mt-1 normal-case">
          Applies on the worker&apos;s next cycle, no restart needed. This single interval drives everything the
          background worker does each pass: inventory discovery, PVE node/cluster health &amp; status, PBS
          protection sync, findings evaluation (including the affinity/overcommit checks), and recommendation
          generation -- there is no separate schedule for any of those today.
        </p>
      </Field>
      <label className="flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          disabled={!isAdmin}
          checked={form.tls_verify_default}
          onChange={(e) => setForm({ ...form, tls_verify_default: e.target.checked })}
        />
        Verify TLS certificates by default for new PVE targets
      </label>
      <Field label="Timezone">
        <select
          className="input"
          disabled={!isAdmin}
          value={form.timezone}
          onChange={(e) => setForm({ ...form, timezone: e.target.value })}
        >
          {timezoneOptions.map((tz) => (
            <option key={tz} value={tz}>
              {tz}
            </option>
          ))}
        </select>
      </Field>
      <div className="pt-2 border-t border-border">
        <div className="text-xs font-semibold text-text mb-1">Rightsizing peak-utilization targets</div>
        <p className="text-[11px] text-muted mb-3 normal-case">
          Rightsizing sizes a workload so its observed peak usage lands at roughly this % of its allocation --
          lower means more headroom (safer, but suggests larger allocations); higher means less headroom (more
          aggressive downsizing, more risk of running hot). A workload already running hotter than its own
          target gets flagged to increase instead of decrease. Memory is usually kept stricter than CPU since a
          tight memory VM pages/OOMs, while a tight CPU VM just schedules slower.
        </p>
        <RightsizingTargetField
          label="CPU target"
          value={form.rightsizing_cpu_peak_target_pct}
          onChange={(v) => setForm({ ...form, rightsizing_cpu_peak_target_pct: v })}
          disabled={!isAdmin}
        />
        <RightsizingTargetField
          label="Memory target"
          value={form.rightsizing_mem_peak_target_pct}
          onChange={(v) => setForm({ ...form, rightsizing_mem_peak_target_pct: v })}
          disabled={!isAdmin}
        />
        <label className="flex items-center gap-2 text-sm text-text mt-3">
          <input
            type="checkbox"
            disabled={!isAdmin}
            checked={form.rightsizing_round_vcpu_even}
            onChange={(e) => setForm({ ...form, rightsizing_round_vcpu_even: e.target.checked })}
          />
          Round vCPU suggestions up to even numbers
        </label>
        <p className="text-[11px] text-muted mt-1 normal-case">
          Off by default. vCPU suggestions are the exact count the math calls for (odd numbers included) --
          memory suggestions always round to the nearest whole GB regardless. Turn this on if your hosts are
          multi-socket (odd core counts can straddle a NUMA node boundary) or you just want a fleet-wide
          even-numbers convention; on a single-socket host this only wastes allocation rounding up.
        </p>
      </div>
      {error && <div className="text-xs text-bad">{error}</div>}
      {saved && !error && <div className="text-xs text-good">Saved.</div>}
      {isAdmin && (
        <button
          type="submit"
          disabled={pending}
          className="px-3 py-1.5 rounded text-sm font-medium bg-ink text-on-ink border border-accent hover:bg-accent/10 disabled:opacity-50"
        >
          {pending ? "Saving…" : "Save settings"}
        </button>
      )}
      <style jsx>{`
        .input {
          width: 100%;
          background: rgb(var(--c-canvas));
          border: 1px solid rgb(var(--c-border));
          border-radius: 6px;
          padding: 6px 8px;
          font-size: 0.875rem;
          color: rgb(var(--c-text));
        }
      `}</style>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-xs text-muted space-y-1">
      <span>{label}</span>
      {children}
    </label>
  );
}

function RightsizingTargetField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 mb-2">
      <span className="text-xs text-muted w-24 shrink-0">{label}</span>
      <input
        type="range"
        min={50}
        max={95}
        step={1}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1"
      />
      <input
        type="number"
        min={50}
        max={95}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="input"
        style={{ width: 64 }}
      />
      <span className="text-xs text-muted">%</span>
    </div>
  );
}

type ShellTokenRow = { target_id: string; target: string; saved: boolean; status: string | null; token: string | null };

/** One line under "Allow the host shell": is the dedicated shell token saved and did its last test pass. */
function ShellTokenStatus() {
  const [rows, setRows] = useState<ShellTokenRow[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetch("/api/shell/status").then((r) => (r.ok ? r.json() : null)).then((d) => { if (alive) setRows(Array.isArray(d) ? d : null); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!rows || rows.length === 0) return null;
  return (
    <div className="text-[11px] text-muted normal-case space-y-0.5">
      {rows.map((r) => (
        <div key={r.target_id}>
          Shell token{rows.length > 1 ? ` (${r.target})` : ""}:{" "}
          {!r.saved ? (
            <span className="text-bad">not set up</span>
          ) : r.status === "valid" ? (
            <span className="text-good">saved and valid ({r.token})</span>
          ) : (
            <span className="text-warn">saved, not tested yet ({r.token}). Press Test Connection on the Credentials page</span>
          )}
          {" · "}
          <a className="text-accent hover:underline" href="/platform/providers">{r.saved ? "Setup guide" : "Set it up"}</a>
        </div>
      ))}
    </div>
  );
}
