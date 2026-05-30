"""Browser pages for people: a paginated list and a person-detail view.

Both are thin clients over the JSON API (`GET /people`, `GET /people/{id}`),
fetched with `Accept: application/json` so the same URLs keep returning JSON to
API clients. Uses the shared layout in `_layout.py`.
"""
from __future__ import annotations

from ._layout import BASE_CSS, header_html

# ── People list ──────────────────────────────────────────────────────────────

PEOPLE_LIST_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>newsgraph — People</title>
  <style>
    {BASE_CSS}
    .wrap {{ max-width: 920px; margin: 0 auto; padding: 28px 24px 60px; }}
    .toolbar {{ display: flex; align-items: center; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }}
    .toolbar h1 {{ font-size: 22px; margin-right: auto; }}
    .toolbar h1 small {{ font-size: 13px; font-weight: 400; color: var(--gray-3); margin-left: 8px; }}
    .search {{
      padding: 9px 14px; border: 1px solid var(--gray-2); border-radius: var(--radius);
      font-size: 14px; width: 220px; background: var(--white);
    }}
    .search:focus {{ outline: none; border-color: var(--blue); }}

    table {{ width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 12px; }}
    thead th {{
      text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .6px;
      color: var(--gray-3); padding: 12px 16px; background: var(--white); border-bottom: 1px solid var(--gray-2);
    }}
    tbody td {{ padding: 13px 16px; font-size: 14px; border-bottom: 1px solid var(--gray-2); background: var(--white); }}
    tbody tr {{ cursor: pointer; transition: background .12s; }}
    tbody tr:hover td {{ background: #f8fafd; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    .rank {{ color: var(--gray-3); font-variant-numeric: tabular-nums; width: 48px; }}
    .name {{ font-weight: 600; color: var(--text); }}
    .num  {{ font-variant-numeric: tabular-nums; color: var(--gray-4); text-align: right; }}
    .arrow {{ color: var(--gray-3); width: 24px; text-align: right; }}

    .pager {{ display: flex; align-items: center; gap: 14px; margin-top: 20px; justify-content: center; }}
    .pager .info {{ font-size: 13px; color: var(--gray-4); }}
    .msg {{ text-align: center; color: var(--gray-4); padding: 50px; }}
  </style>
</head>
<body>
  {header_html(active="people", subtitle="People")}

  <div class="wrap">
    <div class="toolbar">
      <h1>People <small id="total"></small></h1>
      <input class="search" id="search" type="text" placeholder="Filter this page…" />
    </div>

    <div class="card">
      <table>
        <thead>
          <tr>
            <th class="rank">#</th>
            <th>Name</th>
            <th class="num">Mentions</th>
            <th class="num">Aliases</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div id="msg" class="msg" hidden></div>

    <div class="pager">
      <button class="btn ghost" id="prev">← Prev</button>
      <span class="info" id="pageinfo"></span>
      <button class="btn ghost" id="next">Next →</button>
    </div>
  </div>

  <script>
    const params = new URLSearchParams(location.search);
    let page = Math.max(1, parseInt(params.get("page") || "1", 10));
    const size = Math.min(200, Math.max(1, parseInt(params.get("size") || "50", 10)));
    let allItems = [];

    function setURL() {{
      const u = new URL(location);
      u.searchParams.set("page", page);
      u.searchParams.set("size", size);
      history.replaceState(null, "", u);
    }}

    function render(items) {{
      const tbody = document.getElementById("rows");
      tbody.innerHTML = "";
      items.forEach(function(p, i) {{
        const tr = document.createElement("tr");
        tr.onclick = function() {{ location.href = "/people/" + p.id; }};
        tr.innerHTML =
          '<td class="rank">' + ((page - 1) * size + i + 1) + '</td>' +
          '<td class="name"></td>' +
          '<td class="num">' + p.mention_count + '</td>' +
          '<td class="num">' + p.alias_count + '</td>' +
          '<td class="arrow">›</td>';
        tr.children[1].textContent = p.canonical_name;
        tbody.appendChild(tr);
      }});
    }}

    async function load() {{
      setURL();
      const res = await fetch("/people?page=" + page + "&size=" + size,
                              {{ headers: {{ "Accept": "application/json" }} }});
      const data = await res.json();
      allItems = data.items;
      render(allItems);

      document.getElementById("total").textContent = data.total + " total";
      const start = (page - 1) * size + 1;
      const end = (page - 1) * size + data.items.length;
      document.getElementById("pageinfo").textContent =
        data.total === 0 ? "No people yet" : (start + "–" + end + " of " + data.total);
      document.getElementById("prev").disabled = page <= 1;
      document.getElementById("next").disabled = end >= data.total;

      if (data.total === 0) {{
        const msg = document.getElementById("msg");
        msg.hidden = false;
        msg.innerHTML = 'No people yet. Ingest articles via <a href="/docs">the API</a>, then refresh.';
      }}
    }}

    document.getElementById("prev").onclick = function() {{ if (page > 1) {{ page--; load(); }} }};
    document.getElementById("next").onclick = function() {{ page++; load(); }};
    document.getElementById("search").oninput = function(e) {{
      const q = e.target.value.toLowerCase();
      render(allItems.filter(function(p) {{ return p.canonical_name.toLowerCase().includes(q); }}));
    }};

    load().catch(function(err) {{
      const msg = document.getElementById("msg");
      msg.hidden = false;
      msg.textContent = "Failed to load: " + err.message;
    }});
  </script>
</body>
</html>
"""


# ── Person detail ────────────────────────────────────────────────────────────

PERSON_DETAIL_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>newsgraph — Person</title>
  <style>
    {BASE_CSS}
    .wrap {{ max-width: 820px; margin: 0 auto; padding: 28px 24px 60px; }}
    .back {{ font-size: 13px; text-decoration: none; color: var(--gray-4); display: inline-block; margin-bottom: 16px; }}
    .back:hover {{ color: var(--blue); }}

    .person-head {{ padding: 24px 28px; margin-bottom: 22px; }}
    .person-head h1 {{ font-size: 26px; margin-bottom: 12px; }}
    .aliases {{ display: flex; gap: 6px; flex-wrap: wrap; }}

    .section {{ margin-bottom: 28px; }}
    .section h2 {{
      font-size: 13px; text-transform: uppercase; letter-spacing: .6px;
      color: var(--gray-3); margin-bottom: 12px; display: flex; align-items: center; gap: 8px;
    }}
    .section h2 .count {{
      background: var(--gray-2); color: var(--gray-4); font-size: 11px;
      padding: 1px 8px; border-radius: 99px; letter-spacing: 0;
    }}

    .edge {{ padding: 16px 20px; margin-bottom: 10px; }}
    .edge-top {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }}
    .edge-top a {{ font-weight: 600; text-decoration: none; }}
    .edge-top a:hover {{ text-decoration: underline; }}
    .dir {{ color: var(--gray-3); font-size: 13px; }}
    .edge .explain {{ font-size: 14px; color: var(--text); margin-bottom: 8px; line-height: 1.5; }}
    .edge .quote {{
      font-size: 13px; color: var(--gray-4); font-style: italic;
      border-left: 3px solid var(--teal); padding: 4px 0 4px 12px; margin-bottom: 8px;
    }}
    .edge .src {{ font-size: 12px; }}
    .edge .src a {{ color: var(--gray-3); text-decoration: none; word-break: break-all; }}
    .edge .src a:hover {{ color: var(--blue); }}

    .empty-rel {{ font-size: 14px; color: var(--gray-3); padding: 8px 0; }}
    .msg {{ text-align: center; color: var(--gray-4); padding: 50px; }}
  </style>
</head>
<body>
  {header_html(active="people")}

  <div class="wrap">
    <a class="back" href="/people">← All people</a>
    <div id="content"></div>
    <div id="msg" class="msg" hidden></div>
  </div>

  <script>
    // Path is /people/{{id}} (or /people/{{id}}/view); pull the numeric id out.
    const m = location.pathname.match(/\\/people\\/(\\d+)/);
    const id = m ? m[1] : null;

    function edgeHtml(e, direction) {{
      const verb = e.type.replace(/_/g, " ");
      const who = '<a href="/people/' + e.other_person_id + '"></a>';
      const line = direction === "out"
        ? '<span class="pill">' + verb + '</span><span class="dir">→</span>' + who
        : who + '<span class="dir">→</span><span class="pill">' + verb + '</span>';
      const div = document.createElement("div");
      div.className = "edge card";
      div.innerHTML =
        '<div class="edge-top">' + line + '</div>' +
        '<div class="explain"></div>' +
        '<div class="quote"></div>' +
        '<div class="src"><a href="' + e.article_url + '" target="_blank">' + e.article_url + '</a></div>';
      div.querySelector(".edge-top a").textContent = e.other_person_name;
      div.querySelector(".explain").textContent = e.explanation;
      div.querySelector(".quote").textContent = '“' + e.supporting_quote + '”';
      return div;
    }}

    function section(title, items, direction) {{
      const sec = document.createElement("div");
      sec.className = "section";
      sec.innerHTML = '<h2>' + title + ' <span class="count">' + items.length + '</span></h2>';
      if (items.length === 0) {{
        const e = document.createElement("div");
        e.className = "empty-rel";
        e.textContent = "None.";
        sec.appendChild(e);
      }} else {{
        items.forEach(function(it) {{ sec.appendChild(edgeHtml(it, direction)); }});
      }}
      return sec;
    }}

    async function load() {{
      if (!id) throw new Error("no person id in URL");
      const res = await fetch("/people/" + id, {{ headers: {{ "Accept": "application/json" }} }});
      if (res.status === 404) {{
        document.getElementById("msg").hidden = false;
        document.getElementById("msg").textContent = "Person #" + id + " not found.";
        return;
      }}
      const p = await res.json();
      document.title = "newsgraph — " + p.canonical_name;

      const head = document.createElement("div");
      head.className = "person-head card";
      const h1 = document.createElement("h1");
      h1.textContent = p.canonical_name;
      head.appendChild(h1);
      const aliasWrap = document.createElement("div");
      aliasWrap.className = "aliases";
      p.aliases.forEach(function(a) {{
        const c = document.createElement("span");
        c.className = "chip";
        c.textContent = a;
        aliasWrap.appendChild(c);
      }});
      head.appendChild(aliasWrap);

      const content = document.getElementById("content");
      content.appendChild(head);
      content.appendChild(section("Outgoing relationships", p.outgoing, "out"));
      content.appendChild(section("Incoming relationships", p.incoming, "in"));
    }}

    load().catch(function(err) {{
      document.getElementById("msg").hidden = false;
      document.getElementById("msg").textContent = "Failed to load: " + err.message;
    }});
  </script>
</body>
</html>
"""
