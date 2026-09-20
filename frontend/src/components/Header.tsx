import { useEffect, useState } from "react";

function formatClock(date: Date): string {
  return date.toLocaleTimeString("en-IN", { hour12: false });
}

export function Header() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="topbar">
      <div className="topbar-title">
        <span className="live-dot" aria-hidden="true" />
        <span className="topbar-name">Option Desk</span>
      </div>
      <div className="topbar-meta">
        <span>Orders placed here are real — review before clicking</span>
        <span className="topbar-clock">{formatClock(now)}</span>
      </div>
    </header>
  );
}
