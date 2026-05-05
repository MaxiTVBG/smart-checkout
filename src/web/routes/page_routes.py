import csv
import io
import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from ...admin_queries import (
    resolve_db_path,
    connect_readonly,
    get_summary,
    get_inventory_items,
    get_logs,
    trace_uid,
    find_anomalies,
    get_registered_codes,
    get_registered_codes_list,
    table_exists,
    table_names,
    get_table_rows,
    table_columns
)
from ..auth import require_auth, _has_perm, generate_csrf_token, load_config
from ..utils import (
    table_html, item_filters_html, log_filters_html, code_filters_html, table_filters_html, h
)

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")

def _get_common_context(request: Request, user: dict, active_nav: str) -> dict:
    config = load_config()
    roles_def = config.get("web_admin", {}).get("roles", {})
    permissions = roles_def.get(user["role"], {}).get("permissions", [])
    if "*" in permissions:
        # Give all specific permissions for the UI to render correctly
        permissions = [
            "view_dashboard", "view_inventory", "view_logs", "view_trace", 
            "view_tables", "manual_add", "manual_remove", "manage_codes", 
            "export_data", "run_sql", "manage_users"
        ]
        
    return {
        "request": request,
        "role": user["role"],
        "permissions": permissions,
        "active_nav": active_nav,
        "db_path": str(resolve_db_path()),
        "csrf_token": generate_csrf_token(request)
    }

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "view_dashboard"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    with connect_readonly(db_path) as conn:
        summary = get_summary(conn)
        
        # Prepare Chart.js data
        # We need the last 7 days of movements (ADDED/REMOVED)
        # However, summary only gives today's movements.
        # We could query logs for the last 7 days to give good chart data.
        chart_labels = []
        chart_added = []
        chart_removed = []
        try:
            today = datetime.date.today()
            date_list = [(today - datetime.timedelta(days=x)).isoformat() for x in range(6, -1, -1)]
            chart_labels = date_list
            # Simple aggregation
            rows = conn.execute(
                "SELECT substr(timestamp, 1, 10) as dt, action, count(*) as c FROM logs GROUP BY dt, action"
            ).fetchall()
            
            data_map = {}
            for r in rows:
                data_map[(r['dt'], r['action'])] = r['c']
                
            for d in date_list:
                chart_added.append(data_map.get((d, "ADDED"), 0))
                chart_removed.append(data_map.get((d, "REMOVED"), 0))
        except Exception:
            pass

        inventory_html = table_html(summary["inventory_by_class"], ["item_class", "count"], table_links=True)
        session_html = table_html(summary["active_cash_sessions"], ["id", "opened_at", "total_items", "total_price"])
        recent_html = table_html(summary["recent_logs"], ["uid", "item_class", "action", "timestamp"], trace_links=True)
        
        anomalies = find_anomalies(conn, limit=5)
        anomaly_html = table_html(anomalies, ["severity", "type", "uid", "timestamp", "detail"], trace_links=True) if anomalies else "<p class='muted' style='padding:16px;'>All systems nominal.</p>"
        
    context = _get_common_context(request, user, "dashboard")
    context.update({
        "summary": summary,
        "inventory_html": inventory_html,
        "session_html": session_html,
        "recent_html": recent_html,
        "anomaly_html": anomaly_html,
        "chart_labels": chart_labels,
        "chart_added": chart_added,
        "chart_removed": chart_removed,
    })
    return templates.TemplateResponse(request, "dashboard.html", context)

@router.get("/items", response_class=HTMLResponse)
async def items(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "view_inventory"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    params = dict(request.query_params)
    
    with connect_readonly(db_path) as conn:
        result = get_inventory_items(conn, params)
        can_add = _has_perm(user["role"], "manual_add")
        
        add_options = ""
        if can_add:
            codes = get_registered_codes_list(conn)
            add_options = "".join(f'<option value="{h(c["public_uid"])}" data-class="{h(c.get("item_class", ""))}">{h(c["public_uid"])} ({h(c.get("item_class", ""))})</option>' for c in codes)

    context = _get_common_context(request, user, "items")
    context.update({
        "total_rows": result["total"],
        "can_add": can_add,
        "add_options": add_options,
        "item_filters_html": item_filters_html(params),
        "table_html": table_html(result["rows"], result["columns"], trace_links=True),
    })
    return templates.TemplateResponse(request, "items.html", context)

@router.get("/logs")
async def logs(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "view_logs"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    params = dict(request.query_params)
    
    with connect_readonly(db_path) as conn:
        result = get_logs(conn, params)
        
    # Check if export requested
    if "export" in params:
        if not _has_perm(user["role"], "export_data"):
            raise HTTPException(status_code=403, detail="Forbidden")
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(result["columns"])
        for row in result["rows"]:
            writer.writerow([row.get(c) for c in result["columns"]])
        
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=\"movements.csv\""}
        )

    context = _get_common_context(request, user, "logs")
    context.update({
        "total_rows": result["total"],
        "filters_html": log_filters_html(params),
        "table_html": table_html(result["rows"], result["columns"], trace_links=True),
        "export_url": f"{request.url}?export=1&" + str(request.query_params)
    })
    return templates.TemplateResponse(request, "logs.html", context)

@router.get("/trace", response_class=HTMLResponse)
async def trace(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "view_trace"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    uid = request.query_params.get("uid", "").strip()
    context = _get_common_context(request, user, "trace")
    context["uid"] = uid
    
    if not uid:
        return templates.TemplateResponse(request, "trace.html", context)
        
    db_path = resolve_db_path()
    with connect_readonly(db_path) as conn:
        res = trace_uid(conn, uid)
        
    context.update({
        "trace": res,
        "item_html": table_html(res["items"], []),
        "code_html": table_html(res["registered_codes"], []),
        "timeline_html": table_html(res["logs"], ["id", "action", "item_class", "timestamp"]),
        "anomaly_html": table_html(res["anomalies"], ["severity", "type", "timestamp", "detail"]) if res["anomalies"] else ""
    })
    return templates.TemplateResponse(request, "trace.html", context)

@router.get("/codes", response_class=HTMLResponse)
async def codes(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "view_tables"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    params = dict(request.query_params)
    
    with connect_readonly(db_path) as conn:
        result = get_registered_codes(conn, params)
        can_edit = _has_perm(user["role"], "manage_codes")

    context = _get_common_context(request, user, "codes")
    context.update({
        "total_rows": result["total"],
        "can_edit": can_edit,
        "filters_html": code_filters_html(params),
        "table_html": table_html(result["rows"], result["columns"]),
    })
    return templates.TemplateResponse(request, "codes.html", context)

@router.get("/tables", response_class=HTMLResponse)
@router.get("/table", response_class=HTMLResponse)
async def table_view(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "view_tables"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    db_path = resolve_db_path()
    params = dict(request.query_params)
    table_name = params.get("name")
    
    context = _get_common_context(request, user, "tables")
    
    with connect_readonly(db_path) as conn:
        if not table_name:
            tables = [{"table": t, "rows": 0} for t in table_names(conn)]
            for t in tables:
                row = conn.execute(f"SELECT count(*) as c FROM {t['table']}").fetchone()
                t["rows"] = row["c"]
            context["table_html"] = table_html(tables, ["table", "rows"], table_links=True)
            return templates.TemplateResponse(request, "tables.html", context)
            
        if not table_exists(conn, table_name):
            raise HTTPException(status_code=404, detail="Table not found")
            
        result = get_table_rows(conn, table_name, params)
        schema = table_columns(conn, table_name)
        
        config = load_config()
        web_config = config.get("web_admin", {})
        existing_users = {
            "token": {str(u.get("token", "")): u.get("role") for u in web_config.get("users", [])},
            "google": {str(u.get("email", "")).lower(): u.get("role") for u in web_config.get("google_oauth", {}).get("users", [])}
        }
        
        # User management modal logic for auth_logs
        modal_html = ""
        if table_name == "auth_logs" and _has_perm(user["role"], "manage_users"):
            roles_def = web_config.get("roles", {})
            role_options = "".join(f'<option value="{h(rn)}">{h(rn)}</option>' for rn in roles_def.keys())
            modal_html = f"""
            <dialog id="userModal" style="border:1px solid var(--line); border-radius:var(--radius); padding:24px; box-shadow:var(--shadow); width:100%; max-width:400px; background:var(--surface);">
              <form method="dialog" style="float:right; margin-top:-10px; margin-right:-10px;">
                <button style="border:none;background:none;font-size:20px;cursor:pointer;">✕</button>
              </form>
              <h3 style="margin-top:0;" id="userModalTitle">Modify User</h3>
              <div id="userModalCurrentRole" style="margin-bottom:16px;" class="muted"></div>
              <form id="userModalForm" method="post" action="/action/change_role">
                <input type="hidden" name="csrf_token" value="{context['csrf_token']}">
                <input type="hidden" name="auth_type" id="userModalAuthType">
                <input type="hidden" name="user_type" id="userModalUserType">
                <input type="hidden" name="user_id" id="userModalUserId">
                <input type="hidden" name="email" id="userModalEmail">
                <input type="hidden" name="token" id="userModalToken">
                <div class="input-group">
                  <label>Assign Role</label>
                  <select name="role" id="userModalRoleSelect" style="width:100%; padding:8px; border:1px solid var(--line); border-radius:4px;">
                    {role_options}
                  </select>
                </div>
                <div style="display:flex;gap:12px;margin-top:24px;">
                  <button type="submit" class="btn-primary" id="userModalSaveBtn" style="flex:1;">Save</button>
                  <button type="button" class="btn-action remove" id="userModalRemoveBtn" style="flex:1; justify-content:center;" onclick="document.getElementById('userModalForm').action='/action/remove_user'; document.getElementById('userModalForm').submit();">Remove Access</button>
                </div>
              </form>
            </dialog>
            <script>
              function openUserModal(action, method, identifier, currentRole) {{
                  const dialog = document.getElementById('userModal');
                  const form = document.getElementById('userModalForm');
                  
                  document.getElementById('userModalAuthType').value = method;
                  document.getElementById('userModalUserType').value = method;
                  document.getElementById('userModalUserId').value = identifier;
                  document.getElementById('userModalEmail').value = identifier;
                  document.getElementById('userModalToken').value = identifier;
                  
                  const roleText = document.getElementById('userModalCurrentRole');
                  const removeBtn = document.getElementById('userModalRemoveBtn');
                  const title = document.getElementById('userModalTitle');
                  const roleSelect = document.getElementById('userModalRoleSelect');
                  
                  if (action === 'add') {{
                      title.innerText = "Add: " + identifier;
                      form.action = '/action/add_user';
                      roleText.innerText = "Not in system.";
                      removeBtn.style.display = 'none';
                      roleSelect.value = 'viewer';
                  }} else {{
                      title.innerText = "Modify: " + identifier;
                      form.action = '/action/change_role';
                      roleText.innerText = "Current role: " + currentRole;
                      roleSelect.value = currentRole;
                      removeBtn.style.display = 'flex';
                  }}
                  dialog.showModal();
              }}
            </script>
            """
            
        context.update({
            "table_name": table_name,
            "total_rows": result["total"],
            "filters_html": table_filters_html(table_name, params),
            "table_html": table_html(result["rows"], result["columns"], trace_links=True, url=None, table_name=table_name, can_manage_users=_has_perm(user["role"], "manage_users"), existing_users=existing_users),
            "schema_html": table_html(schema, ["cid","name","type","notnull","dflt_value","pk"]),
            "modal_html": modal_html,
        })
        return templates.TemplateResponse(request, "tables.html", context)

@router.get("/users", response_class=HTMLResponse)
async def users_view(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "manage_users"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    config = load_config()
    web_config = config.get("web_admin", {})
    tokens = web_config.get("users", [])
    oauth = web_config.get("google_oauth", {}).get("users", [])
    roles_def = web_config.get("roles", {})
    
    tokens_table = table_html(
        [{"token": u.get("token", ""), "role": u.get("role", "")} for u in tokens],
        ["token", "role"]
    )
    oauth_table = table_html(
        [{"email": u.get("email", ""), "role": u.get("role", "")} for u in oauth],
        ["email", "role"]
    )
    
    roles_list = [{"role": name, "permissions": ", ".join(data.get("permissions", []))} for name, data in roles_def.items()]
    roles_table = table_html(roles_list, ["role", "permissions"])
    
    role_options = "".join(f'<option value="{h(rn)}">{h(rn)}</option>' for rn in roles_def.keys())
    
    context = _get_common_context(request, user, "users")
    context.update({
        "total_users": len(tokens) + len(oauth),
        "tokens_table": tokens_table,
        "oauth_table": oauth_table,
        "roles_table": roles_table,
        "role_options": role_options
    })
    return templates.TemplateResponse(request, "users.html", context)

@router.get("/sql", response_class=HTMLResponse)
@router.post("/sql", response_class=HTMLResponse)
async def sql_view(request: Request, user: dict = Depends(require_auth)):
    if not _has_perm(user["role"], "run_sql"):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    context = _get_common_context(request, user, "sql")
    context["sql"] = ""
    
    if request.method == "POST":
        form = await request.form()
        sql = form.get("sql", "").strip()
        context["sql"] = sql
        
        if sql:
            if not sql.lower().lstrip().startswith("select"):
                context["error"] = "Only SELECT queries are allowed."
            elif ";" in sql and not sql.strip().endswith(";"):
                 context["error"] = "Multiple statements are not allowed."
            else:
                db_path = resolve_db_path()
                try:
                    with connect_readonly(db_path) as conn:
                        rows = conn.execute(sql).fetchmany(301)
                        if not rows:
                            context["result_html"] = "<p class='muted'>Query executed successfully (0 rows).</p>"
                        else:
                            truncated = len(rows) > 300
                            if truncated:
                                rows = rows[:300]
                            cols = list(rows[0].keys())
                            context["result_html"] = table_html([dict(r) for r in rows], cols)
                            context["truncated"] = truncated
                except Exception as e:
                    context["error"] = f"SQL Error: {e}"
                    
    return templates.TemplateResponse(request, "sql.html", context)
