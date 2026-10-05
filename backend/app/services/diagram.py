"""Architecture diagrams for Archie (TA): his architecture description → a .drawio file laid out the way a person
would draw it (the approved sample in docs/reference/diagram-sample/), plus reading back a user-edited .drawio.

Layout rules (docs/02-architecture.md "Architecture diagrams"):
  title + subtitle · Sources panel on the left (outside AWS) · AWS Cloud → Region boundary · the main data path on ONE
  centre line (a compute step with details becomes a container with coloured cards) · Destinations panel on the right ·
  supporting services on a bottom row, each under the shape it serves · legend · footer.
  Every edge has explicit exit/entry points so arrows are straight; captions are separate text cells.
Icons are the real draw.io AWS 2024 set (mxgraph.aws4.*); the styles below are the ones search_shapes returned for
the approved sample, generalised per service category.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
import xml.etree.ElementTree as ET  # noqa: S405 — parses our own output and user uploads without DTDs (refused below)
import zlib
from xml.sax.saxutils import quoteattr

INK, MUTED = "#232F3E", "#545B64"
CATEGORY = {"compute": "#ED7100", "integration": "#E7157B", "storage": "#7AA116", "database": "#C925D1",
            "security": "#DD344C", "management": "#E7157B", "networking": "#8C4FFF", "analytics": "#8C4FFF",
            "ml": "#01A88D", "frontend": "#DD344C"}
# icon key → (resIcon name, category)
SERVICES = {
    "lambda": ("lambda", "compute"), "ec2": ("ec2", "compute"), "ecs": ("ecs", "compute"), "fargate": ("fargate", "compute"),
    "ecr": ("ecr", "compute"), "batch": ("batch", "compute"),
    "api_gateway": ("api_gateway", "integration"), "sqs": ("sqs", "integration"), "sns": ("sns", "integration"),
    "eventbridge": ("eventbridge", "integration"), "step_functions": ("step_functions", "integration"),
    "mq": ("mq", "integration"), "appsync": ("appsync", "integration"), "transfer_family": ("transfer_family", "integration"),
    "s3": ("s3", "storage"), "efs": ("elastic_file_system", "storage"),
    "dynamodb": ("dynamodb", "database"), "rds": ("rds", "database"), "aurora": ("aurora", "database"),
    "elasticache": ("elasticache", "database"),
    "kinesis": ("kinesis_data_streams", "analytics"), "firehose": ("kinesis_data_firehose", "analytics"),
    "glue": ("glue", "analytics"), "athena": ("athena", "analytics"), "msk": ("managed_streaming_for_kafka", "analytics"),
    "secrets_manager": ("secrets_manager", "security"), "kms": ("key_management_service", "security"),
    "cognito": ("cognito", "security"), "waf": ("waf", "security"),
    "cloudwatch": ("cloudwatch_2", "management"), "systems_manager": ("systems_manager", "management"),
    "cloudformation": ("cloudformation", "management"), "cloudfront": ("cloudfront", "networking"),
    "route53": ("route_53", "networking"), "elb": ("elastic_load_balancing", "networking"), "vpc": ("vpc", "networking"),
}
# stand-alone glyphs (not the square resource icon)
GLYPHS = {"iam_role": ("role", "#DD344C"), "cloudwatch_logs": ("cloudwatch_logs", "#E7157B"), "alarm": ("alarm", "#E7157B"),
          "lambda_layer": ("lambda_function", "#ED7100"), "s3_bucket": ("bucket", "#7AA116"), "sqs_queue": ("queue", "#E7157B"),
          "alb": ("application_load_balancer", "#8C4FFF"), "nat_gateway": ("nat_gateway", "#8C4FFF"),
          "internet_gateway": ("internet_gateway", "#8C4FFF"), "vpc_endpoint": ("endpoints", "#8C4FFF")}
ICONS = sorted([*SERVICES, *GLYPHS, "users", "external_system"])

PTS = ("points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],"
       "[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]];")
CLOUD = ("points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],"
         "[0,0.75],[0,0.5],[0,0.25]];outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=1;"
         "container=0;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_aws_cloud_alt;"
         f"strokeColor={INK};fillColor=none;verticalAlign=top;align=left;spacingLeft=30;fontColor={INK};dashed=0;")
REGION = ("sketch=0;outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=1;shape=mxgraph.aws4.group;"
          "grIcon=mxgraph.aws4.group_region;strokeColor=#879196;fillColor=none;verticalAlign=top;align=left;spacingLeft=30;"
          "fontColor=#879196;dashed=1;container=0;")
PANEL = f"rounded=1;arcSize=4;dashed=1;fillColor=#F7F8FA;strokeColor=#AAB7B8;verticalAlign=top;fontStyle=1;fontSize=12;fontColor={INK};spacingTop=6;html=1;"
EDGE = {
    "data": f"html=1;edgeStyle=orthogonalEdgeStyle;rounded=1;endArrow=block;endFill=1;strokeWidth=2;strokeColor=#7C5CFF;fontColor={INK};fontSize=11;labelBackgroundColor=#FFFFFF;",
    "error": "html=1;edgeStyle=orthogonalEdgeStyle;rounded=1;endArrow=block;endFill=1;dashed=1;strokeWidth=2;strokeColor=#DD344C;fontColor=#DD344C;fontSize=10;labelBackgroundColor=#FFFFFF;",
    "support": f"html=1;edgeStyle=orthogonalEdgeStyle;rounded=1;endArrow=open;dashed=1;strokeWidth=1.5;strokeColor=#879196;fontColor={MUTED};fontSize=10;labelBackgroundColor=#FFFFFF;",
}
CARD_COLOURS = [("#E8F5E9", "#2E7D32"), ("#E3F2FD", "#1565C0"), ("#FFF8E1", "#B7791F"), ("#FCE4EC", "#AD1457")]


def icon_style(key: str) -> str:
    if key in SERVICES:
        res, cat = SERVICES[key]
        return (f"sketch=0;{PTS}outlineConnect=0;fontColor={INK};fillColor={CATEGORY[cat]};strokeColor=#ffffff;dashed=0;"
                f"verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
                f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};")
    if key in GLYPHS:
        shape, colour = GLYPHS[key]
        return (f"sketch=0;outlineConnect=0;fontColor={INK};gradientColor=none;fillColor={colour};strokeColor=none;dashed=0;"
                f"verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
                f"pointerEvents=1;shape=mxgraph.aws4.{shape};")
    if key == "users":
        return (f"sketch=0;outlineConnect=0;fontColor={INK};gradientColor=none;strokeColor={INK};fillColor=#ffffff;dashed=0;"
                "verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
                "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.users;")
    # external system / anything we have no AWS icon for: a neutral server-ish box
    return f"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor=#F2F3F3;strokeColor={MUTED};fontColor={INK};fontSize=11;"


def validate(arch: dict) -> list[str]:
    """Problems that would make a broken diagram; returned to Archie so he fixes them."""
    problems: list[str] = []
    nodes = [n for k in ("sources", "path", "destinations", "support") for n in arch.get(k, [])]
    ids = [n["id"] for n in nodes]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append(f"duplicate node ids: {sorted(dup)}")
    if not arch.get("path"):
        problems.append("`path` needs at least one node (the main data path inside AWS)")
    for n in nodes:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", n["id"]):
            problems.append(f"node id {n['id']!r} must be letters, digits or _ (start with a letter)")
        if n["icon"] not in ICONS:
            problems.append(f"node {n['id']}: unknown icon {n['icon']!r}; use one of {ICONS}")
    for n in nodes:
        if re.search(r"tfstate|terraform state|state bucket", f"{n.get('label', '')} {n.get('sub', '')}", re.I):
            problems.append(f"node {n['id']}: the Terraform state is the platform's, not part of the running flow; leave it out")
    for s in arch.get("support", []):
        if s.get("under") not in ids:
            problems.append(f"support node {s['id']}: `under` must be another node's id, got {s.get('under')!r}")
    for e in arch.get("edges", []):
        for end in ("from", "to"):
            if e[end] not in ids:
                problems.append(f"edge {e['from']}→{e['to']}: unknown node {e[end]!r}")
    return problems


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Doc:
    def __init__(self) -> None:
        self.cells: list[str] = []
        self.geo: dict[str, tuple[float, float, float, float]] = {}

    def box(self, cid: str, value: str, style: str, x: float, y: float, w: float, h: float) -> None:
        self.geo[cid] = (x, y, w, h)
        self.cells.append(f'<mxCell id="{cid}" value={quoteattr(value)} style={quoteattr(style)} vertex="1" parent="1">'
                          f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" as="geometry"/></mxCell>')

    def text(self, cid: str, html: str, x: float, y: float, w: float, h: float, extra: str = "") -> None:
        self.box(cid, html, f"text;html=1;align=center;verticalAlign=top;whiteSpace=wrap;fontColor={INK};{extra}", x, y, w, h)

    def edge(self, cid: str, s: str, t: str, kind: str, label: str, exit_, entry, via=()) -> None:
        pts = (f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
               if exit_ and entry else "")
        bends = ('<Array as="points">' + "".join(f'<mxPoint x="{x:.0f}" y="{y:.0f}"/>' for x, y in via) + "</Array>") if via else ""
        self.cells.append(f'<mxCell id="{cid}" value={quoteattr(_esc(label))} style={quoteattr(EDGE.get(kind, EDGE["data"]) + pts)} '
                          f'edge="1" parent="1" source="{s}" target="{t}"><mxGeometry relative="1" as="geometry">{bends}</mxGeometry></mxCell>')


def build(arch: dict, footer: str = "") -> str:
    """Architecture description → .drawio XML."""
    d = _Doc()
    CY, ICON = 340, 60
    sources, path, dests, support = arch.get("sources", []), arch["path"], arch.get("destinations", []), arch.get("support", [])
    centre: dict[str, tuple[float, float]] = {}   # node id → (cx, cy) of its icon / container
    shape_of: dict[str, str] = {}                 # node id → the cell edges attach to

    def icon(n: dict, cx: float, cy: float) -> None:
        cid = n["id"]
        if n["icon"] == "external_system":
            d.box(cid, f"<b>{_esc(n['label'])}</b>", icon_style("external_system"), cx - 70, cy - 32, 140, 64)
            if n.get("sub"):
                d.text(f"{cid}_lbl", f"<span style='font-size:10px;color:{MUTED}'>{_esc(n['sub'])}</span>", cx - 95, cy + 36, 190, 30)
        else:
            d.box(cid, "", icon_style(n["icon"]), cx - ICON / 2, cy - ICON / 2, ICON, ICON)
            cap = f"<b>{_esc(n['label'])}</b>" + (f"<br><span style='font-size:10px;color:{MUTED}'>{_esc(n['sub'])}</span>" if n.get("sub") else "")
            d.text(f"{cid}_lbl", cap, cx - 95, cy + ICON / 2 + 4, 190, 44, "fontSize=12;")
        centre[cid], shape_of[cid] = (cx, cy), cid

    # columns: sources panel | (cloud starts) path… | destinations panel. Gaps leave room for arrow captions above lines.
    x = 40
    src_x = x
    x += 290 if sources else 0
    cloud_x = x
    x += 70
    path_cols: list[tuple[dict, float, float]] = []
    for n in path:
        w = 400 if n.get("details") else 210
        path_cols.append((n, x, w))
        x += w + 80
    dst_x = x
    width = dst_x + (260 if dests else 0) + 60
    stack = max(len(sources), len(dests), 1)
    container_h = max([120 + 70 * min(len(n.get("details", [])), 4) for n in path if n.get("details")] or [0])
    BY = max(CY + 150 * (stack - 1) + 230, CY + container_h / 2 + 170, 710)
    has_support = bool(support)
    region_bottom = (BY + 125) if has_support else max(CY + 150 * (stack - 1) + 120, CY + container_h / 2 + 60)

    # supporting services: under the shape each serves, never overlapping, never outside the region. Placed BEFORE the
    # boxes are drawn, so the AWS Cloud / Region boxes grow to contain them (they used to spill out on the right).
    want = {n["id"]: n.get("under", "") for n in support}
    col_x = {n["id"]: px + w / 2 for n, px, w in path_cols}
    col_x.update({n["id"]: src_x + 110 for n in sources})
    col_x.update({n["id"]: dst_x + 120 for n in dests})
    region_left = cloud_x + 20
    sup_x: dict[str, float] = {}
    last = region_left + 110 - 220
    for n in sorted(support, key=lambda s: col_x.get(want[s["id"]], width / 2)):
        cx = max(col_x.get(want[n["id"]], width / 2), last + 220, region_left + 120)
        sup_x[n["id"]] = last = cx
    if sup_x:
        width = max(width, max(sup_x.values()) + 120 + 60)
    # a retry/redrive bracket between stacked destinations needs room on the right for its caption
    dest_ids = {n["id"] for n in dests}
    if any(e.get("kind") == "error" and e["from"] in dest_ids and e["to"] in dest_ids for e in arch.get("edges", [])):
        width += 190
    # supporting services hang off one connector from their lane (no tangle of lines); what each one does for the
    # shape it serves (the edge label, e.g. "import logger") goes under its own icon
    sup_ids = {n["id"] for n in support}
    sup_note: dict[str, str] = {}
    for e in arch.get("edges", []):
        if (e["from"] in sup_ids) != (e["to"] in sup_ids) and e.get("label"):
            sid = e["from"] if e["from"] in sup_ids else e["to"]
            sup_note[sid] = e["label"]

    # header
    d.text("title", f"<b>{_esc(arch.get('title', 'Architecture'))}</b>", 0, 16, width, 30, "fontSize=22;")
    d.text("subtitle", _esc(arch.get("subtitle", "")), 0, 50, width, 20, f"fontSize=11;fontColor={MUTED};fontStyle=2;")
    # boundaries first (they sit behind)
    d.box("cloud", "AWS Cloud", CLOUD, cloud_x, 96, width - cloud_x - 20, region_bottom - 56)
    d.box("region", "Region eu-west-1 (Ireland)", REGION, cloud_x + 20, 136, width - cloud_x - 60, region_bottom - 116)
    if arch.get("network"):  # e.g. "No VPC: the Lambda isn't attached to one (AWS-managed network)"
        d.text("network", f"<i>{_esc(arch['network'])}</i>", width - 560, 142, 500, 18, f"fontSize=10;align=right;fontColor={MUTED};")
    if support:  # a quiet lane that says what the bottom row is
        lane_l = min(sup_x.values()) - 115
        d.box("support_lane", "Supporting services: permissions, shared code, logs",
              f"rounded=1;arcSize=6;dashed=1;dashPattern=1 3;fillColor=#FAFBFC;strokeColor=#C3CCD1;verticalAlign=top;align=left;"
              f"spacingLeft=12;spacingTop=4;fontSize=10;fontColor={MUTED};html=1;",
              lane_l, BY - 62, max(sup_x.values()) + 115 - lane_l, 172)
    panel_h = (CY - 180) + 150 * (stack - 1) + 130   # top label → last icon + its caption
    if sources:
        d.box("src_panel", "Sources", PANEL, src_x, 180, 220, panel_h)
        for i, n in enumerate(sources):
            icon(n, src_x + 110, CY + 150 * i)
    if dests:
        d.box("dst_panel", "Destinations", PANEL, dst_x, 180, 240, panel_h)
        for i, n in enumerate(dests):
            icon(n, dst_x + 120, CY + 150 * i)

    # the main data path on the centre line
    for n, px, w in path_cols:
        cards = (n.get("details") or [])[:4]
        if cards:
            h = 120 + 70 * len(cards)
            top = CY - h / 2
            cat = SERVICES.get(n["icon"], ("", "compute"))[1]
            colour = CATEGORY.get(cat, "#ED7100")
            d.box(n["id"], "", f"rounded=1;arcSize=5;fillColor=#FFFFFF;strokeColor={colour};strokeWidth=2;html=1;", px, top, w, h)
            d.box(f"{n['id']}_icon", "", icon_style(n["icon"]), px + w / 2 - 28, top + 12, 56, 56)
            d.text(f"{n['id']}_title", f"<b>{_esc(n['label'])}</b>", px, top + 72, w, 20, f"fontSize=13;fontColor={colour};")
            d.text(f"{n['id']}_sub", _esc(n.get("sub", "")), px, top + 92, w, 18, f"fontSize=10;fontColor={MUTED};")
            for i, c in enumerate(cards):
                fill, stroke = CARD_COLOURS[i % len(CARD_COLOURS)]
                head, _, rest = c.partition(":")
                body = f"<b>{_esc(head)}</b>" + (f"<br>{_esc(rest.strip())}" if rest else "")
                d.box(f"{n['id']}_card{i}", body, f"rounded=1;arcSize=6;whiteSpace=wrap;html=1;align=left;verticalAlign=top;spacingLeft=10;"
                      f"spacingTop=4;fillColor={fill};strokeColor={stroke};fontSize=11;fontColor={INK};", px + 20, top + 118 + 70 * i, w - 40, 62)
            centre[n["id"]], shape_of[n["id"]] = (px + w / 2, CY), n["id"]
        else:
            icon(n, px + w / 2, CY)

    # supporting services on the bottom row (positions worked out above), each with what it does in its caption
    for n in support:
        note = sup_note.get(n["id"], "")
        icon({**n, "sub": " · ".join(x for x in (n.get("sub", ""), f"↳ {note}" if note else "") if x)}, sup_x[n["id"]], BY)
    # one dashed connector per served shape: from the lane straight up to it
    if support:
        lx, ly_, lw, _ = d.geo["support_lane"]
        for k, (target, group) in enumerate({n.get("under", ""): None for n in support}.items()):
            if target not in d.geo:
                continue
            tbx, tby, tbw, tbh = d.geo[target]
            x = min(max(tbx + tbw / 2, lx + 30), lx + lw - 30)
            d.edge(f"sup{k}", "support_lane", target, "support", "", (round((x - lx) / lw, 4), 0), (round(min(max((x - tbx) / tbw, 0.05), 0.95), 4), 1))

    # edges with explicit attach points so arrows run straight. Captions are separate text cells placed next to the
    # line (above a horizontal arrow, beside a vertical one), sized to the free space so they never cross an icon.
    tone = {"data": INK, "error": "#DD344C", "support": MUTED}
    edges = arch.get("edges", [])
    pairs = {(e["from"], e["to"]) for e in edges}

    def caption(cid: str, text: str, kind: str, x: float, y: float, w: float, where: str) -> None:
        if not text:
            return
        h = 46
        valign, top = {"above": ("bottom", y - h - 3), "below": ("top", y + 3), "right": ("middle", y - h / 2)}[where]
        d.text(cid, _esc(text), x, top, w, h, f"fontSize=10;verticalAlign={valign};align={'left' if where == 'right' else 'center'};"
               f"fontColor={tone.get(kind, INK)};")

    for i, e in enumerate(edges):
        s, t = shape_of.get(e["from"]), shape_of.get(e["to"])
        if not s or not t or (e["from"] in sup_ids) != (e["to"] in sup_ids):
            continue  # links to supporting services are drawn as the lane's connector (above)
        kind, label = e.get("kind", "data"), e.get("label", "")
        (sx, sy), (tx, ty) = centre[e["from"]], centre[e["to"]]
        bx, by, bw, bh = d.geo[s]
        tbx, tby, tbw, tbh = d.geo[t]
        if abs(sy - ty) < 5:                       # same row → straight horizontal
            reverse = (e["to"], e["from"]) in pairs
            second = reverse and tx < sx           # a request/reply pair: the reply runs just below the request
            dy = (12 if second else -12) if reverse else 0
            ys, yt = (sy + dy - by) / bh, (ty + dy - tby) / tbh
            ex, en = ((1, ys), (0, yt)) if tx > sx else ((0, ys), (1, yt))
            d.edge(f"e{i}", s, t, kind, "", (ex[0], round(ex[1], 4)), (en[0], round(en[1], 4)))
            x1, x2 = (bx + bw, tbx) if tx > sx else (tbx + tbw, bx)
            w = max(110, x2 - x1 - 12)
            caption(f"e{i}_cap", label, kind, (x1 + x2) / 2 - w / 2, sy + dy, w, "below" if second else "above")
        elif ty > sy and abs(tx - sx) < 40:        # straight down
            if kind == "error" and abs(ty - sy) <= 160 and ty < BY:   # within a stack: bracket on the right, clear of captions
                rx = sx + 110
                d.edge(f"e{i}", s, t, kind, "", (1, 0.5), (1, 0.5), via=((rx, sy), (rx, ty)))
                caption(f"e{i}_cap", label, kind, rx + 8, (sy + ty) / 2, 170, "right")
            else:
                d.edge(f"e{i}", s, t, kind, "", (0.5, 1), (0.5, 0))
                caption(f"e{i}_cap", label, kind, sx + 8, (by + bh + tby) / 2, 170, "right")
        elif ty > sy:                              # down to another column: leave the bottom above the target
            fx = min(max((tx - bx) / bw, 0.05), 0.95) if bx <= tx <= bx + bw else 0.5
            d.edge(f"e{i}", s, t, kind or "support", "", (round(fx, 4), 1), (0.5, 0))
            caption(f"e{i}_cap", label, kind or "support", bx + fx * bw + 8, (by + bh + tby) / 2, 170, "right")
        else:                                      # up (e.g. a supporting service feeding the path)
            d.edge(f"e{i}", s, t, kind, "", (0.5, 0), (round(min(max((sx - tbx) / tbw, 0.05), 0.95), 4), 1))
            caption(f"e{i}_cap", label, kind, sx + 8, (by + tby + tbh) / 2, 170, "right")

    # legend + footer
    ly = region_bottom + 40
    d.box("legend", "<b>Legend</b>&nbsp;&nbsp;&nbsp;<span style='color:#7C5CFF'>━━</span> Data path&nbsp;&nbsp;&nbsp;"
          "<span style='color:#DD344C'>- - -</span> Error / retry path&nbsp;&nbsp;&nbsp;"
          "<span style='color:#879196'>- - -</span> Security / observability",
          f"rounded=1;arcSize=10;whiteSpace=wrap;html=1;align=left;spacingLeft=14;fillColor=#FFFFFF;strokeColor=#D5DBDB;fontSize=11;fontColor={INK};",
          40, ly, width - 80, 40)
    d.text("footer", _esc(footer or "Generated by Orkestra · Archie (TA) · editable in draw.io"), 0, ly + 50, width, 18, f"fontSize=9;fontColor={MUTED};")

    body = "\n        ".join(d.cells)
    return f"""<mxfile host="orkestra" type="device">
  <diagram id="arch" name="Architecture">
    <mxGraphModel dx="1600" dy="1000" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{width:.0f}" pageHeight="{ly + 90:.0f}" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        {body}
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


def editor_url(xml: str) -> str:
    """diagrams.net link that carries the diagram in the #fragment (never sent to a server)."""
    raw = urllib.parse.quote(xml, safe="~()*!.'")
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = base64.b64encode(comp.compress(raw.encode()) + comp.flush()).decode()
    return f"https://app.diagrams.net/?grid=0&splash=0#R{urllib.parse.quote(data, safe='')}"


# ── reading a user-edited diagram back ──────────────────────────────────────────────────────────────────────────────
def _model(xml: str) -> ET.Element:
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", xml, re.I):
        raise ValueError("DTD / entity declarations are not allowed in a diagram")
    root = ET.fromstring(xml)  # noqa: S314
    if root.tag == "mxGraphModel":
        return root
    diagram = root.find("diagram") if root.tag == "mxfile" else None
    if diagram is None:
        raise ValueError("not a draw.io file (no <mxfile>/<diagram>)")
    model = diagram.find("mxGraphModel")
    if model is not None:
        return model
    text = (diagram.text or "").strip()  # compressed: base64 → raw deflate → URL-encoded XML
    try:
        inflated = zlib.decompress(base64.b64decode(text), -15).decode()
        return ET.fromstring(urllib.parse.unquote(inflated))  # noqa: S314
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"couldn't read the compressed diagram: {exc}") from exc


def _label(v: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (v or "").replace("&nbsp;", " "))).strip()


def cells(xml: str) -> dict[str, dict]:
    out = {}
    for c in _model(xml).iter("mxCell"):
        cid = c.get("id")
        if cid in ("0", "1") or cid is None:
            continue
        g = c.find("mxGeometry")
        out[cid] = {"label": _label(c.get("value", "")), "edge": c.get("edge") == "1", "source": c.get("source"),
                    "target": c.get("target"),
                    "x": float(g.get("x", 0)) if g is not None and c.get("edge") != "1" else 0.0,
                    "y": float(g.get("y", 0)) if g is not None and c.get("edge") != "1" else 0.0}
    return out


def diff(old_xml: str, new_xml: str) -> dict:
    """What the user changed in draw.io: added / removed / relabelled / moved shapes and connections."""
    a, b = cells(old_xml), cells(new_xml)
    name = lambda m, cid: m[cid]["label"] or cid  # noqa: E731
    out = {"added": [], "removed": [], "relabelled": [], "moved": [], "connections_added": [], "connections_removed": []}
    for cid in b.keys() - a.keys():
        if b[cid]["edge"]:
            out["connections_added"].append(f"{name(b, b[cid]['source']) if b[cid]['source'] in b else '?'} → "
                                            f"{name(b, b[cid]['target']) if b[cid]['target'] in b else '?'}" + (f" ({b[cid]['label']})" if b[cid]["label"] else ""))
        elif b[cid]["label"]:
            out["added"].append(b[cid]["label"])
    for cid in a.keys() - b.keys():
        if a[cid]["edge"]:
            out["connections_removed"].append(f"{name(a, a[cid]['source']) if a[cid]['source'] in a else '?'} → "
                                              f"{name(a, a[cid]['target']) if a[cid]['target'] in a else '?'}")
        elif a[cid]["label"]:
            out["removed"].append(a[cid]["label"])
    for cid in a.keys() & b.keys():
        if a[cid]["label"] != b[cid]["label"] and (a[cid]["label"] or b[cid]["label"]):
            out["relabelled"].append(f"“{a[cid]['label']}” → “{b[cid]['label']}”")
        elif not a[cid]["edge"] and abs(a[cid]["x"] - b[cid]["x"]) + abs(a[cid]["y"] - b[cid]["y"]) > 20 and a[cid]["label"]:
            out["moved"].append(a[cid]["label"])
    return out


def diff_summary(d: dict) -> str:
    parts = []
    for key, title in (("added", "Added"), ("removed", "Removed"), ("relabelled", "Relabelled"), ("moved", "Moved"),
                       ("connections_added", "New connections"), ("connections_removed", "Removed connections")):
        if d[key]:
            parts.append(f"{title}: " + "; ".join(d[key][:12]))
    return ". ".join(parts) or "No changes found"
