"use client";

import { useEffect, useState } from "react";
import { OneLineCommand, WRAPPER_TROUBLE } from "@/components/WrapperUpdatePanel";
import type { Link } from "@/lib/hostScripts";

/** Update the maintenance wrapper on a Proxmox Backup Server, in place: the same one line as for the Proxmox hosts, and the server ticks itself when it answers with the new version. */
export default function PbsWrapperUpdate({ targetId, name, onClose }: { targetId: string; name: string; onClose?: () => void }) {
  const [expected, setExpected] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [since] = useState(Date.now());
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(async () => {
      setNow(Date.now());
      try {
        const r = await fetch(`/api/pbs/servers/${targetId}/ssh/check`, { method: "POST" });
        const d = await r.json();
        if (r.ok && d.wrapper_version) setVersion(d.wrapper_version);
      } catch { /* try again next time */ }
    }, 10000);  // an SSH login each time: gently, and only while this panel is open
    return () => clearInterval(t);
  }, [targetId]);

  const done = !!expected && !!version && version.localeCompare(expected, undefined, { numeric: true }) >= 0;
  const slow = !done && now - since > 3 * 60_000;
  return (
    <div className="mt-2 border border-border rounded p-3 text-xs space-y-3 bg-surface2/30">
      <div className="text-sm font-semibold text-text">Update the maintenance wrapper on {name} <span className="text-xs font-normal text-muted">· about a minute · PyXie runs nothing on the server: you paste one line</span></div>
      <div className="text-muted"><b>What this is:</b> the small helper PyXie asks the backup server to run when it patches or restarts it. Nothing about how it is protected changes.</div>
      <div><div className="font-medium text-text mb-0.5">1. Open a root shell on {name}</div><div className="text-muted">In its web page choose <b>Shell</b> (or SSH in as root).</div></div>
      <div><div className="font-medium text-text mb-0.5">2. Paste this one line and press Enter</div><OneLineCommand targetId={targetId} onLink={(l: Link | null) => l && setExpected(l.wrapper_version)} /></div>
      <div><div className="font-medium text-text mb-0.5">3. Watch it turn green</div>
        {done ? <div className="rounded border border-good/50 bg-good/10 p-2 text-good">✓ {name} is on wrapper {version}. {onClose && <button type="button" className="ml-2 text-muted hover:text-text" onClick={onClose}>Done</button>}</div>
          : <div className="text-warn">waiting… {version ? `still ${version}` : "no answer yet"} <span className="text-muted">(checked every few seconds; the server&apos;s SSH fingerprint is already trusted, so nothing to confirm)</span></div>}
      </div>
      <details open={slow}><summary className="cursor-pointer text-text">Something went wrong?{slow ? " No new version after 3 minutes; start here." : ""}</summary>
        <table className="w-full mt-1"><tbody>{WRAPPER_TROUBLE.map(([a, b, c]) => <tr key={a} className="border-t border-border align-top"><td className="py-1 pr-3"><code>{a}</code></td><td className="pr-3 text-muted">{b}</td><td>{c}</td></tr>)}</tbody></table>
      </details>
    </div>
  );
}
