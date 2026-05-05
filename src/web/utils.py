import html
import urllib.parse
from typing import Any, List, Dict

def h(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)

def columns_from_rows(rows: List[Dict[str, Any]], fallback: List[str] | None = None) -> List[str]:
    if rows:
        columns: List[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        return columns
    return fallback or []

def options(values: List[str], selected: str) -> str:
    labels = {"": "All", "yes": "Yes", "no": "No", "asc": "Asc", "desc": "Desc"}
    return "".join(
        f'<option value="{h(value)}"{" selected" if value == selected else ""}>{h(labels.get(value, value))}</option>'
        for value in values
    )

def _url(path: str, **params: Any) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    query = urllib.parse.urlencode(clean, doseq=True)
    return path + (f"?{query}" if query else "")

def table_html(
    rows: List[Dict[str, Any]],
    columns: List[str],
    trace_links: bool = False,
    table_links: bool = False,
    table_name: str = "",
    can_manage_users: bool = False,
    existing_users: Dict[str, Dict[str, str]] | None = None,
) -> str:
    columns = columns or columns_from_rows(rows)
    if not columns:
        return "<p class='muted' style='padding:16px;'>No columns.</p>"
    if not rows:
        return "<p class='muted' style='padding:16px;'>No rows.</p>"
    
    show_auth_actions = table_name == "auth_logs" and can_manage_users
    head = "".join(f"<th>{h(col)}</th>" for col in columns)
    if show_auth_actions:
        head += "<th>Actions</th>"

    body_rows = []
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col)
            cell = h(value)
            if trace_links and col == "uid" and value:
                cell = f'<a href="{_url("/trace", uid=value)}">{h(value)}</a>'
            if table_links and col == "table" and value:
                cell = f'<a href="{_url("/table", name=value)}">{h(value)}</a>'
            if col == "in_stock":
                cell = '<span class="status in">IN</span>' if value == 1 else '<span class="status out">OUT</span>'
            if col == "active":
                cell = '<span class="status in">YES</span>' if value == 1 else '<span class="status out">NO</span>'
            if col == "severity" and value == "error":
                cell = '<span class="status out">ERROR</span>'
            elif col == "severity" and value == "warning":
                cell = '<span class="status neutral">WARN</span>'
            cells.append(f"<td>{cell}</td>")
        
        if show_auth_actions:
            method = row.get("method")
            identifier = row.get("identifier")
            btn = ""
            if method in ("google", "token") and identifier:
                current_role = ""
                if existing_users:
                    users_dict = existing_users.get(method, {})
                    lookup_id = identifier if method == "token" else identifier.lower()
                    current_role = users_dict.get(lookup_id, "")
                
                if current_role:
                    btn = f'''<button type="button" class="btn-action neutral" onclick="openUserModal('modify', '{h(method)}', '{h(identifier)}', '{h(current_role)}')">Modify</button>'''
                else:
                    btn = f'''<button type="button" class="btn-action add" onclick="openUserModal('add', '{h(method)}', '{h(identifier)}', '')">Add</button>'''
            cells.append(f"<td>{btn}</td>")

        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>"

def item_filters_html(params: dict[str, str]) -> str:
    return f"""
    <form class="filters" method="get" action="{_url('/items')}">
        <div style="display:grid; gap:6px;">
            <label>Search</label>
            <input name="search" value="{h(params.get('search',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Class</label>
            <input name="class" value="{h(params.get('class',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Status</label>
            <select name="in_stock">{options(['','yes','no'], params.get('in_stock',''))}</select>
        </div>
        <div style="display:grid; gap:6px;">
            <label>Sort</label>
            <select name="sort">{options(['uid','item_class','in_stock','last_seen','movement_count'], params.get('sort','uid'))}</select>
        </div>
        <div style="display:grid; gap:6px;">
            <label>Order</label>
            <select name="order">{options(['asc','desc'], params.get('order','asc'))}</select>
        </div>
        <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
    </form>
    """

def log_filters_html(params: dict[str, str]) -> str:
    return f"""
    <form class="filters" method="get" action="{_url('/logs')}">
        <div style="display:grid; gap:6px;">
            <label>Search</label>
            <input name="search" value="{h(params.get('search',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>UID</label>
            <input name="uid" value="{h(params.get('uid',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Class</label>
            <input name="class" value="{h(params.get('class',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Action</label>
            <select name="action">{options(['','ADDED','REMOVED'], params.get('action',''))}</select>
        </div>
        <div style="display:grid; gap:6px;">
            <label>From</label>
            <input type="date" name="from" value="{h(params.get('from',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>To</label>
            <input type="date" name="to" value="{h(params.get('to',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Sort</label>
            <input name="sort" value="{h(params.get('sort','timestamp'))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Order</label>
            <select name="order">{options(['desc','asc'], params.get('order','desc'))}</select>
        </div>
        <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
    </form>
    """

def code_filters_html(params: dict[str, str]) -> str:
    return f"""
    <form class="filters" method="get" action="{_url('/codes')}">
        <div style="display:grid; gap:6px;">
            <label>Search</label>
            <input name="search" value="{h(params.get('search',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Class</label>
            <input name="class" value="{h(params.get('class',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Active</label>
            <select name="active">{options(['','yes','no'], params.get('active',''))}</select>
        </div>
        <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
    </form>
    """

def table_filters_html(table: str, params: dict[str, str]) -> str:
    return f"""
    <form class="filters" method="get" action="{_url('/table')}">
        <input type="hidden" name="name" value="{h(table)}">
        <div style="display:grid; gap:6px;">
            <label>Search</label>
            <input name="search" value="{h(params.get('search',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Sort</label>
            <input name="sort" value="{h(params.get('sort',''))}">
        </div>
        <div style="display:grid; gap:6px;">
            <label>Order</label>
            <select name="order">{options(['desc','asc'], params.get('order','desc'))}</select>
        </div>
        <button type="submit" class="btn-primary" style="width:100px;">Apply</button>
    </form>
    """
