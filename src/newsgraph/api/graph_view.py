"""Interactive knowledge-graph page (vis-network), served at `/` and `/graph/view`.

Thin client over `GET /graph`. Uses the shared layout in `_layout.py` so it
matches the people pages. No template engine, no build step.
"""
from __future__ import annotations

from ._layout import BASE_CSS, header_html

GRAPH_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>newsgraph — Knowledge Graph</title>
  <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
  <style>
    {BASE_CSS}

    html, body {{ height: 100%; }}
    body {{ display: flex; flex-direction: column; overflow: hidden; }}

    .stats-bar {{
      background: var(--white);
      border-bottom: 1px solid var(--gray-2);
      padding: 0 28px;
      height: 44px;
      display: flex;
      align-items: center;
      gap: 24px;
      flex-shrink: 0;
    }}
    .stat {{ display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--gray-4); }}
    .stat strong {{ font-size: 15px; font-weight: 700; color: var(--text); }}
    .stat-dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--blue); }}
    .stat-dot.edge {{ background: var(--teal); }}
    .loading-pill {{ font-size: 12px; color: var(--gray-3); animation: pulse 1.4s ease-in-out infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}

    #graph {{ flex: 1; min-height: 0; position: relative; }}

    .legend {{
      position: absolute; top: 16px; left: 20px;
      padding: 12px 16px; font-size: 12px; min-width: 160px; display: none;
    }}
    .legend h3 {{
      font-size: 11px; text-transform: uppercase; letter-spacing: .6px;
      color: var(--gray-3); margin-bottom: 10px;
    }}
    .legend-item {{ display: flex; align-items: center; gap: 8px; color: var(--gray-4); margin-bottom: 6px; }}
    .legend-item:last-child {{ margin-bottom: 0; }}
    .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; background: var(--blue); }}
    .legend-line {{ width: 18px; height: 2px; border-radius: 1px; background: var(--teal); opacity: .7; flex-shrink: 0; }}

    .hint {{
      position: absolute; bottom: 16px; right: 20px;
      background: var(--navy); color: rgba(255,255,255,.7);
      font-size: 11px; padding: 6px 12px; border-radius: 99px;
      pointer-events: none; opacity: 0; transition: opacity .3s;
    }}
    .hint.show {{ opacity: 1; }}

    #empty {{ position: absolute; inset: 0; display: none; flex-direction: column;
              align-items: center; justify-content: center; }}
    #empty.show {{ display: flex; }}
    .empty-card {{ padding: 36px 48px; text-align: center; max-width: 440px; }}
    .empty-card h2 {{ font-size: 18px; margin-bottom: 8px; }}
    .empty-card p {{ font-size: 14px; color: var(--gray-4); line-height: 1.6; margin-bottom: 18px; }}
    .empty-card code {{ background: var(--gray-1); border: 1px solid var(--gray-2);
                        border-radius: 4px; padding: 1px 6px; font-size: 12px; }}
  </style>
</head>
<body>
  {header_html(active="graph", subtitle="Knowledge Graph Explorer")}

  <div class="stats-bar">
    <div class="stat"><div class="stat-dot"></div><strong id="node-count">—</strong> people</div>
    <div class="stat"><div class="stat-dot edge"></div><strong id="edge-count">—</strong> relationships</div>
    <span class="loading-pill" id="loading">Loading graph…</span>
  </div>

  <div id="graph">
    <div class="legend card" id="legend">
      <h3>Legend</h3>
      <div class="legend-item"><div class="legend-dot"></div>Person (size = mentions)</div>
      <div class="legend-item"><div class="legend-line"></div>Relationship</div>
      <div class="legend-item" style="font-size:11px;color:var(--gray-3);margin-top:8px;">
        Double-click a node to open its detail
      </div>
    </div>

    <div id="empty">
      <div class="empty-card card">
        <h2>Graph is empty</h2>
        <p>Ingest some articles first using
          <code>POST /articles</code> or <code>POST /rescan</code>, then refresh.</p>
        <a class="btn" href="/docs">Open API Docs</a>
      </div>
    </div>

    <div class="hint" id="hint">Double-click a node to view detail</div>
  </div>

  <script>
    async function main() {{
      const res = await fetch("/graph", {{ headers: {{ "Accept": "application/json" }} }});
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();

      document.getElementById("loading").style.display = "none";
      document.getElementById("node-count").textContent = data.nodes.length;
      document.getElementById("edge-count").textContent = data.edges.length;

      if (data.nodes.length === 0) {{
        document.getElementById("empty").classList.add("show");
        return;
      }}
      document.getElementById("legend").style.display = "block";

      const nodes = new vis.DataSet(data.nodes.map(function(n) {{
        return {{
          id: n.id, label: n.label, value: Math.max(1, n.mentions),
          title: "<b>" + n.label + "</b><br>" + n.mentions + " mention" + (n.mentions !== 1 ? "s" : ""),
          color: {{ background: "#1a6de0", border: "#0f4fab",
                    highlight: {{ background: "#3b8ef3", border: "#1a6de0" }},
                    hover: {{ background: "#3b8ef3", border: "#1a6de0" }} }},
          font: {{ color: "#ffffff", size: 12 }},
        }};
      }}));

      const edges = new vis.DataSet(data.edges.map(function(e) {{
        return {{
          from: e.source, to: e.target,
          label: e.type.replace(/_/g, " "),
          title: e.type.replace(/_/g, " ") + (e.count > 1 ? " ×" + e.count : ""),
          arrows: "to",
          color: {{ color: "#0bbcd4", highlight: "#1a6de0", hover: "#1a6de0", opacity: 0.7 }},
          font: {{ size: 9, color: "#64748b", align: "middle", strokeWidth: 2, strokeColor: "#f4f6fa" }},
          smooth: {{ type: "dynamic" }},
        }};
      }}));

      const options = {{
        nodes: {{ shape: "dot",
                  scaling: {{ min: 10, max: 50, label: {{ enabled: true, min: 11, max: 22 }} }},
                  shadow: {{ enabled: true, size: 6, x: 2, y: 2, color: "rgba(0,0,0,.15)" }} }},
        edges: {{ width: 1.5, selectionWidth: 3 }},
        physics: {{ stabilization: {{ iterations: 250, updateInterval: 25 }},
                    barnesHut: {{ gravitationalConstant: -9000, centralGravity: 0.3,
                                  springLength: 150, springConstant: 0.04, damping: 0.09 }} }},
        interaction: {{ hover: true, tooltipDelay: 100 }},
      }};

      const network = new vis.Network(document.getElementById("graph"),
                                      {{ nodes: nodes, edges: edges }}, options);

      const hint = document.getElementById("hint");
      let shown = false;
      network.on("hoverNode", function() {{ if (!shown) {{ hint.classList.add("show"); shown = true; }} }});
      network.on("doubleClick", function(p) {{ if (p.nodes.length) location.href = "/people/" + p.nodes[0]; }});
      network.on("stabilized", function() {{ setTimeout(function() {{ hint.classList.remove("show"); }}, 3000); }});
    }}
    main().catch(function(err) {{
      document.getElementById("loading").textContent = "Failed to load: " + err.message;
    }});
  </script>
</body>
</html>
"""
