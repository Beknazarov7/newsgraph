"""Shared HTML chrome for the browser-facing pages.

One design system (colors, header, buttons, cards) reused by the graph view and
the people pages, so the front-end looks like a single product rather than a set
of one-off pages. Styled to match the agents.inc brand: dark-navy header, white
body, blue + teal accents.
"""
from __future__ import annotations

# Color palette + reset + header/nav + shared components. Inlined (no build step)
# so the whole front-end ships inside the Python package.
BASE_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --navy:    #0d1b2e;
  --navy-2:  #122038;
  --blue:    #1a6de0;
  --blue-lt: #3b8ef3;
  --blue-dk: #0f4fab;
  --teal:    #0bbcd4;
  --white:   #ffffff;
  --gray-1:  #f4f6fa;
  --gray-2:  #e2e8f0;
  --gray-3:  #94a3b8;
  --gray-4:  #64748b;
  --text:    #1e293b;
  --radius:  8px;
  --shadow:  0 2px 12px rgba(0,0,0,.08);
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  background: var(--gray-1);
  color: var(--text);
  min-height: 100vh;
}

a { color: var(--blue); }

/* ── Header ─────────────────────────────────────────────── */
header {
  background: var(--navy);
  color: var(--white);
  padding: 0 28px;
  height: 56px;
  display: flex;
  align-items: center;
  gap: 24px;
  box-shadow: 0 2px 8px rgba(0,0,0,.3);
  position: sticky;
  top: 0;
  z-index: 50;
}
.logo { display: flex; align-items: center; gap: 10px; text-decoration: none; color: inherit; }
.logo-icon { width: 28px; height: 28px; flex-shrink: 0; }
.logo-name { font-size: 15px; font-weight: 700; letter-spacing: .3px; color: var(--white); }
.logo-name span { color: var(--teal); }
header .divider { width: 1px; height: 24px; background: rgba(255,255,255,.15); }
header .subtitle { font-size: 13px; color: rgba(255,255,255,.5); }
.nav-links { display: flex; gap: 6px; margin-left: auto; }
.nav-links a {
  color: rgba(255,255,255,.75);
  text-decoration: none;
  font-size: 13px;
  padding: 6px 12px;
  border-radius: var(--radius);
  transition: background .15s, color .15s;
}
.nav-links a:hover { background: rgba(255,255,255,.1); color: var(--white); }
.nav-links a.active { background: rgba(255,255,255,.12); color: var(--white); }
.nav-links a.primary { background: var(--blue); color: var(--white); }
.nav-links a.primary:hover { background: var(--blue-lt); }

/* ── Shared components ──────────────────────────────────── */
.btn {
  display: inline-block;
  padding: 9px 22px;
  background: var(--blue);
  color: var(--white);
  text-decoration: none;
  border-radius: var(--radius);
  font-size: 13px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: background .15s;
}
.btn:hover { background: var(--blue-lt); }
.btn.ghost {
  background: var(--white);
  color: var(--blue);
  border: 1px solid var(--gray-2);
}
.btn.ghost:hover { background: var(--gray-1); }
.btn:disabled { opacity: .4; cursor: default; }

.card {
  background: var(--white);
  border: 1px solid var(--gray-2);
  border-radius: 12px;
  box-shadow: var(--shadow);
}

.chip {
  display: inline-block;
  background: var(--gray-1);
  border: 1px solid var(--gray-2);
  color: var(--gray-4);
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 99px;
}

.pill {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 99px;
  background: rgba(11,188,212,.12);
  color: #088ca0;
  text-transform: capitalize;
}
"""


def header_html(active: str = "", subtitle: str = "") -> str:
    """Render the shared top bar. `active` is one of 'graph' | 'people' to
    highlight the current nav item."""

    def cls(name: str) -> str:
        return ' class="active"' if active == name else ""

    sub = f'<span class="subtitle">{subtitle}</span>' if subtitle else ""
    return f"""
  <header>
    <a class="logo" href="/">
      <svg class="logo-icon" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="5"  cy="14" r="4" fill="#1a6de0"/>
        <circle cx="23" cy="5"  r="3" fill="#0bbcd4"/>
        <circle cx="23" cy="23" r="3" fill="#0bbcd4"/>
        <circle cx="14" cy="14" r="3" fill="rgba(255,255,255,.25)" stroke="rgba(255,255,255,.4)" stroke-width="1"/>
        <line x1="9" y1="14" x2="20" y2="7"  stroke="rgba(255,255,255,.35)" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="9" y1="14" x2="20" y2="21" stroke="rgba(255,255,255,.35)" stroke-width="1.5" stroke-linecap="round"/>
      </svg>
      <span class="logo-name">news<span>graph</span></span>
    </a>
    <div class="divider"></div>
    {sub}
    <nav class="nav-links">
      <a href="/"{cls('graph')}>Graph</a>
      <a href="/people"{cls('people')}>People</a>
      <a href="/docs" class="primary">API Docs</a>
    </nav>
  </header>"""
