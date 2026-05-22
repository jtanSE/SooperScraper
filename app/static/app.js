// Sooper Scraper — vanilla JS frontend.
// Hash routes:
//   #/jobs               list
//   #/jobs/new           create form
//   #/jobs/:id           detail + run history
//   #/jobs/:id/edit      edit form

const root = document.getElementById("app");

// --- tiny helpers ----------------------------------------------------------

const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(c) : c);
  }
  return node;
};

const fmtDate = (s) => (s ? new Date(s).toLocaleString() : "—");
const fmtDuration = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

const humanSchedule = (job) => {
  const c = job.schedule_config || {};
  switch (job.schedule_type) {
    case "hourly":   return "Every hour";
    case "interval": return `Every ${c.minutes} min`;
    case "daily":    return `Daily at ${String(c.hour).padStart(2,"0")}:${String(c.minute).padStart(2,"0")} UTC`;
    case "weekly":   return `Weekly on ${c.day_of_week} at ${String(c.hour).padStart(2,"0")}:${String(c.minute).padStart(2,"0")} UTC`;
    case "cron":     return `Cron: ${c.expression}`;
    default:         return job.schedule_type;
  }
};

const banner = (message) => {
  const b = el("div", { class: "banner error" }, message);
  root.prepend(b);
  setTimeout(() => b.remove(), 6000);
};

// --- API ------------------------------------------------------------------

const api = async (method, path, body) => {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let detail;
    try { detail = (await resp.json()).detail; } catch { detail = resp.statusText; }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (resp.status === 204) return null;
  return resp.json();
};

const Jobs = {
  list:   ()           => api("GET",    "/api/jobs"),
  get:    (id)         => api("GET",    `/api/jobs/${id}`),
  create: (payload)    => api("POST",   "/api/jobs", payload),
  update: (id, payload)=> api("PATCH",  `/api/jobs/${id}`, payload),
  remove: (id)         => api("DELETE", `/api/jobs/${id}`),
  pause:  (id)         => api("POST",   `/api/jobs/${id}/pause`),
  resume: (id)         => api("POST",   `/api/jobs/${id}/resume`),
  run:    (id)         => api("POST",   `/api/jobs/${id}/run`),
  runs:   (id, limit=50, offset=0) => api("GET", `/api/jobs/${id}/runs?limit=${limit}&offset=${offset}`),
  setCredentials:   (id, p) => api("POST",   `/api/jobs/${id}/credentials`, p),
  clearCredentials: (id)    => api("DELETE", `/api/jobs/${id}/credentials`),
  setCookies:       (id, p) => api("POST",   `/api/jobs/${id}/cookies`, p),
  clearCookies:     (id)    => api("DELETE", `/api/jobs/${id}/cookies`),
};

// --- views ----------------------------------------------------------------

async function viewList() {
  root.replaceChildren(el("h2", {}, "Scheduled jobs"));
  let jobs;
  try { jobs = await Jobs.list(); }
  catch (e) { banner(`Failed to load jobs: ${e.message}`); return; }

  if (jobs.length === 0) {
    root.appendChild(el("p", { class: "muted" }, "No jobs yet. ",
      el("a", { href: "#/jobs/new" }, "Create one")));
    return;
  }

  const table = el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Name"),
      el("th", {}, "Schedule"),
      el("th", {}, "Status"),
      el("th", {}, "Last run"),
      el("th", {}, "Next run"),
      el("th", {}, "Actions"),
    )),
    el("tbody", {}, ...jobs.map(jobRow)),
  );
  root.appendChild(table);
}

function jobRow(job) {
  const refresh = () => { window.location.hash = "#/jobs"; viewList(); };
  return el("tr", {},
    el("td", {},
      el("a", { href: `#/jobs/${job.id}` }, job.name),
      job.description ? el("div", { class: "muted" }, job.description) : null,
    ),
    el("td", {}, humanSchedule(job)),
    el("td", {}, el("span", { class: `badge ${job.status}` }, job.status)),
    el("td", {}, fmtDate(job.last_run_at)),
    el("td", {}, fmtDate(job.next_run_at)),
    el("td", {}, el("div", { class: "actions" },
      el("button", { onclick: async () => { try { await Jobs.run(job.id); banner(`Triggered "${job.name}"`); } catch (e) { banner(e.message); } } }, "Run now"),
      job.status === "paused"
        ? el("button", { onclick: async () => { try { await Jobs.resume(job.id); refresh(); } catch (e) { banner(e.message); } } }, "Resume")
        : el("button", { onclick: async () => { try { await Jobs.pause(job.id);  refresh(); } catch (e) { banner(e.message); } } }, "Pause"),
      el("a", { class: "btn", href: `#/jobs/${job.id}/edit` }, "Edit"),
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(`Delete "${job.name}"?`)) return;
        try { await Jobs.remove(job.id); refresh(); } catch (e) { banner(e.message); }
      } }, "Delete"),
    )),
  );
}

function jobFormFields(initial) {
  const isEdit = !!initial;
  const data = initial || {
    name: "", description: "", urls: [""], extractors: [{ name: "", selector: "", attribute: "text", multiple: false }],
    schedule_type: "daily", schedule_config: { hour: 9, minute: 0 },
    auth: null, has_credentials: false, has_cookies: false, zip_records: false, sort: null, notify: null,
  };

  const nameI  = el("input", { type: "text", required: true, value: data.name });
  const descI  = el("textarea", {}, data.description || "");
  const urlsT  = el("textarea", { required: true }, (data.urls || []).join("\n"));

  const extractorsBox = el("div", { class: "extractors" });
  const addExtractorRow = (e = { name: "", selector: "", attribute: "text", multiple: false }) => {
    const nameI = el("input", { type: "text", placeholder: "name", value: e.name, required: true });
    const selI  = el("input", { type: "text", placeholder: "CSS selector", value: e.selector, required: true });
    const attrI = el("input", { type: "text", placeholder: "attribute (default: text)", value: e.attribute || "text" });
    const multI = el("input", { type: "checkbox" });
    if (e.multiple) multI.checked = true;
    const removeBtn = el("button", { type: "button", class: "danger", onclick: () => row.remove() }, "×");
    const row = el("div", { class: "extractor-row" }, nameI, selI, attrI,
      el("label", { class: "muted" }, multI, " all"), removeBtn);
    extractorsBox.appendChild(row);
  };
  (data.extractors || []).forEach(addExtractorRow);

  const zipI = el("input", { type: "checkbox" });
  if (data.zip_records) zipI.checked = true;

  const sortFieldI = el("input", {
    type: "text",
    placeholder: "extractor name (e.g. premium). Blank = no sort.",
    value: data.sort ? data.sort.field : "",
  });
  const sortDirSel = el("select", {},
    el("option", { value: "desc" }, "Descending (largest first)"),
    el("option", { value: "asc"  }, "Ascending (smallest first)"),
  );
  sortDirSel.value = data.sort ? data.sort.direction : "desc";
  const sortNumericI = el("input", { type: "checkbox" });
  sortNumericI.checked = data.sort ? data.sort.numeric : true;

  const schedSel = el("select", {},
    el("option", { value: "interval" }, "Every N minutes"),
    el("option", { value: "hourly"   }, "Hourly"),
    el("option", { value: "daily"    }, "Daily"),
    el("option", { value: "weekly"   }, "Weekly"),
    el("option", { value: "cron"     }, "Custom cron"),
  );
  schedSel.value = data.schedule_type;
  const schedConfig = el("div", { class: "row" });

  const renderSchedConfig = () => {
    schedConfig.replaceChildren();
    const c = data.schedule_config || {};
    if (schedSel.value === "interval") {
      const minI = el("input", { type: "number", min: 1, max: 1440, value: c.minutes ?? 30 });
      const startI = el("input", { type: "time", value: c.start_at || "" });
      schedConfig.append(
        el("label", {}, "Minutes between runs", minI),
        el("label", {},
          "Anchor time (UTC, optional)",
          startI,
          el("div", { class: "help" }, "Leave blank to start now. Set to e.g. 00:03 with 5 min interval to fire at :03, :08, :13, ..."),
        ),
      );
      schedConfig._collect = () => {
        const out = { minutes: +minI.value };
        if (startI.value) out.start_at = startI.value;
        return out;
      };
    } else if (schedSel.value === "daily") {
      const hourI = el("input", { type: "number", min: 0, max: 23, value: c.hour ?? 9 });
      const minI  = el("input", { type: "number", min: 0, max: 59, value: c.minute ?? 0 });
      schedConfig.append(
        el("label", {}, "Hour (UTC)", hourI),
        el("label", {}, "Minute",     minI),
      );
      schedConfig._collect = () => ({ hour: +hourI.value, minute: +minI.value });
    } else if (schedSel.value === "weekly") {
      const daySel = el("select", {}, ...["mon","tue","wed","thu","fri","sat","sun"].map(d =>
        el("option", { value: d }, d)));
      daySel.value = c.day_of_week ?? "mon";
      const hourI = el("input", { type: "number", min: 0, max: 23, value: c.hour ?? 9 });
      const minI  = el("input", { type: "number", min: 0, max: 59, value: c.minute ?? 0 });
      schedConfig.append(
        el("label", {}, "Day",       daySel),
        el("label", {}, "Hour (UTC)", hourI),
        el("label", {}, "Minute",     minI),
      );
      schedConfig._collect = () => ({ day_of_week: daySel.value, hour: +hourI.value, minute: +minI.value });
    } else if (schedSel.value === "cron") {
      const exprI = el("input", { type: "text", placeholder: "*/15 * * * *", value: c.expression || "" });
      schedConfig.append(el("label", {}, "Cron expression", exprI));
      schedConfig._collect = () => ({ expression: exprI.value.trim() });
    } else {
      schedConfig._collect = () => ({});
    }
  };
  schedSel.addEventListener("change", renderSchedConfig);
  renderSchedConfig();

  // --- auth section ---------------------------------------------------------

  const authEnable = el("input", { type: "checkbox" });
  if (data.auth) authEnable.checked = true;
  const authBody = el("div", { class: "auth-body" });

  const a = data.auth || {
    login_url: "", method: "post",
    username_field: "", password_field: "", extra_fields: {},
    success_check: { type: "selector_absent", value: "" },
  };
  const loginUrlI = el("input", { type: "text", placeholder: "https://example.com/login", value: a.login_url });
  const methodSel = el("select", {}, el("option", { value: "post" }, "POST"), el("option", { value: "get" }, "GET"));
  methodSel.value = a.method;
  const userFieldI = el("input", { type: "text", placeholder: "form field for username", value: a.username_field });
  const passFieldI = el("input", { type: "text", placeholder: "form field for password", value: a.password_field });
  const extrasT = el("textarea", { placeholder: "key=value, one per line" },
    Object.entries(a.extra_fields || {}).map(([k, v]) => `${k}=${v}`).join("\n"));
  const checkTypeSel = el("select", {},
    ...["selector_absent", "selector_present", "url_contains", "url_not_contains", "text_contains", "text_absent"]
      .map(t => el("option", { value: t }, t)));
  checkTypeSel.value = a.success_check.type;
  const checkValueI = el("input", { type: "text", placeholder: "e.g. input[name='password']", value: a.success_check.value });

  // Credentials inputs. For edit mode they default empty — leaving them blank
  // means "don't change". For create they're required when auth is enabled.
  const credUserI = el("input", { type: "text", placeholder: isEdit ? "leave blank to keep current" : "username", autocomplete: "off" });
  const credPassI = el("input", { type: "password", placeholder: isEdit ? "leave blank to keep current" : "password", autocomplete: "new-password" });

  const credsStatus = el("span", { class: "muted" },
    data.has_credentials ? "Credentials stored." : "No credentials stored.");

  const help = (text) => el("div", { class: "help" }, text);

  authBody.append(
    el("p", { class: "help" },
      "Form-login describes ", el("em", {}, "how"), " the site's login page works (URLs and HTML field names). Your actual credentials go in the second block below."),
    el("div", { class: "row" },
      el("label", {}, "Login URL", loginUrlI, help("The URL of the login page — where the form POSTs to.")),
      el("label", {}, "Method", methodSel, help("Usually POST. Use GET only if the login form's `method` attribute says so.")),
    ),
    el("div", { class: "row" },
      el("label", {},
        "Username field name", userFieldI,
        help("The HTML `name` attribute of the username/email input. Example: if the page has `<input name=\"login_username\">`, enter login_username here. NOT your actual username."),
      ),
      el("label", {},
        "Password field name", passFieldI,
        help("The HTML `name` attribute of the password input. Example: `<input name=\"login_password\">` → enter login_password. NOT your actual password."),
      ),
    ),
    el("label", {},
      "Extra hidden fields (key=value per line)", extrasT,
      help("Hidden inputs the form sends along (CSRF tokens, submit button name, redirect URLs). View the page source on the login page and copy any `<input type=\"hidden\">` name/value pairs."),
    ),
    el("div", { class: "row" },
      el("label", {},
        "Success check type", checkTypeSel,
        help("How we decide that login worked. After we POST credentials we look at the response and apply this check."),
      ),
      el("label", {},
        "Success check value", checkValueI,
        help("Depends on the type — see examples below."),
      ),
    ),
    el("div", { class: "help" },
      el("strong", {}, "Success-check examples:"), el("br"),
      el("code", {}, "selector_absent"), " + ", el("code", {}, "input[name='login_password']"),
      " — after login the password field should be gone (most reliable for WordPress).",
      el("br"),
      el("code", {}, "url_not_contains"), " + ", el("code", {}, "/login"),
      " — final URL should no longer be the login page.",
      el("br"),
      el("code", {}, "text_contains"), " + ", el("code", {}, "Log out"),
      " — page now shows a logout link.",
      el("br"),
      el("code", {}, "selector_present"), " + ", el("code", {}, ".user-menu"),
      " — page now shows an account widget.",
    ),
    el("hr"),
    el("p", { class: "help" },
      el("strong", {}, "Your credentials"), " — what you type to log in. Encrypted at rest, never returned by the API."),
    el("div", {}, credsStatus),
    el("div", { class: "row" },
      el("label", {}, "Username (your actual username/email)", credUserI),
      el("label", {}, "Password (your actual password)", credPassI),
    ),
  );

  const renderAuth = () => { authBody.style.display = authEnable.checked ? "" : "none"; };
  authEnable.addEventListener("change", renderAuth);
  renderAuth();

  const collectAuth = () => {
    if (!authEnable.checked) return { enabled: false };
    const extra = {};
    extrasT.value.split(/\r?\n/).forEach(line => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const i = trimmed.indexOf("=");
      if (i < 0) return;
      extra[trimmed.slice(0, i).trim()] = trimmed.slice(i + 1).trim();
    });
    return {
      enabled: true,
      auth: {
        login_url: loginUrlI.value.trim(),
        method: methodSel.value,
        username_field: userFieldI.value.trim(),
        password_field: passFieldI.value.trim(),
        extra_fields: extra,
        success_check: { type: checkTypeSel.value, value: checkValueI.value.trim() },
      },
      credentialsEntered: !!(credUserI.value && credPassI.value),
      credentials: (credUserI.value && credPassI.value)
        ? { username: credUserI.value, password: credPassI.value }
        : null,
    };
  };

  // --- collect --------------------------------------------------------------

  const collect = () => ({
    name: nameI.value.trim(),
    description: descI.value.trim() || null,
    urls: urlsT.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    extractors: [...extractorsBox.querySelectorAll(".extractor-row")].map(row => {
      const [n, s, a, lab] = row.children;
      return {
        name: n.value.trim(),
        selector: s.value.trim(),
        attribute: a.value.trim() || "text",
        multiple: lab.querySelector("input[type=checkbox]").checked,
      };
    }),
    schedule: { type: schedSel.value, ...schedConfig._collect() },
    zip_records: zipI.checked,
    sort: sortFieldI.value.trim()
      ? { field: sortFieldI.value.trim(), direction: sortDirSel.value, numeric: sortNumericI.checked }
      : null,
    clear_sort: !sortFieldI.value.trim(),
  });

  // --- notifications section -------------------------------------------------

  const notifyEnable = el("input", { type: "checkbox" });
  if (data.notify) notifyEnable.checked = true;
  const notifyBody = el("div", { class: "auth-body" });
  const webhookI = el("input", {
    type: "text",
    placeholder: "https://discord.com/api/webhooks/.../...",
    value: data.notify ? data.notify.discord_webhook_url : "",
  });
  const notifySuccI = el("input", { type: "checkbox" });
  notifySuccI.checked = data.notify ? data.notify.on_success : true;
  const notifyErrI = el("input", { type: "checkbox" });
  notifyErrI.checked = data.notify ? data.notify.on_error : true;
  const topNI = el("input", {
    type: "number", min: 0, max: 25,
    value: data.notify && data.notify.include_top_n ? data.notify.include_top_n : 0,
  });
  const alertFieldI = el("input", {
    type: "text", placeholder: "extractor name, e.g. premium",
    value: data.notify && data.notify.alert_field ? data.notify.alert_field : "",
  });
  const alertThreshI = el("input", {
    type: "number", step: "any", placeholder: "e.g. 50000000 for $50M",
    value: data.notify && data.notify.alert_threshold != null ? data.notify.alert_threshold : "",
  });
  const previewFieldsI = el("input", {
    type: "text", placeholder: "ticker, side, premium, size, price",
    value: data.notify && data.notify.preview_fields ? data.notify.preview_fields.join(", ") : "",
  });
  notifyBody.append(
    el("p", { class: "help" },
      "Discord will receive an embed every time the job runs. ",
      el("strong", {}, "To get the URL:"), " in your Discord server: ",
      el("em", {}, "Channel settings → Integrations → Webhooks → New Webhook → Copy Webhook URL"),
      ". Treat it like a password — anyone with the URL can post in that channel.",
    ),
    el("label", {}, "Discord webhook URL", webhookI),
    el("label", { class: "muted" }, notifySuccI, " Notify on success (success / partial runs)"),
    el("label", { class: "muted" }, notifyErrI, " Notify on error"),
    el("hr"),
    el("p", { class: "help" },
      el("strong", {}, "Optional: include records in the embed."),
      " Requires Zip records to be on. The list uses your sort order, so set Sort first."),
    el("label", {}, "Show top N records (0 = off, max 25)", topNI),
    el("label", {},
      "Preview fields (comma-separated, in display order)", previewFieldsI,
      el("div", { class: "help" },
        "Which extractor columns to include in each record line. Blank = all non-empty up to 8. ",
        el("strong", {}, "Tip:"), " put the most important fields first. ",
        el("code", {}, "Buy"), " / ", el("code", {}, "Sell"),
        " values get a green/red dot."),
    ),
    el("div", { class: "row" },
      el("label", {},
        "Alert when this field is at least…", alertFieldI,
        el("div", { class: "help" }, "Extractor name to compare against the threshold."),
      ),
      el("label", {},
        "…this number", alertThreshI,
        el("div", { class: "help" }, "Use the raw number (50000000 for $50M)."),
      ),
    ),
  );
  const renderNotify = () => { notifyBody.style.display = notifyEnable.checked ? "" : "none"; };
  notifyEnable.addEventListener("change", renderNotify);
  renderNotify();

  const collectNotify = () => {
    if (!notifyEnable.checked || !webhookI.value.trim()) {
      return { enabled: notifyEnable.checked, payload: null };
    }
    const payload = {
      discord_webhook_url: webhookI.value.trim(),
      on_success: notifySuccI.checked,
      on_error: notifyErrI.checked,
      include_top_n: +topNI.value || 0,
    };
    const af = alertFieldI.value.trim();
    const at = alertThreshI.value.trim();
    if (af && at) {
      payload.alert_field = af;
      payload.alert_threshold = +at;
    }
    const pf = previewFieldsI.value
      .split(",").map(s => s.trim()).filter(Boolean);
    if (pf.length) payload.preview_fields = pf;
    return { enabled: true, payload };
  };

  // --- cookies section ------------------------------------------------------

  const cookieEnable = el("input", { type: "checkbox" });
  if (data.has_cookies) cookieEnable.checked = true;
  const cookieBody = el("div", { class: "auth-body" });
  const cookieT = el("textarea", {
    placeholder: data.has_cookies
      ? "Cookies stored. Paste again to replace, or leave blank to keep current."
      : "wordpress_logged_in_xxx=...; other_cookie=...",
    rows: 4,
  });
  cookieBody.append(
    el("p", { class: "help" },
      el("strong", {}, "How to get the cookie string:"), el("br"),
      "1. Log into the site in Chrome/Firefox.", el("br"),
      "2. Open DevTools (F12) → ", el("strong", {}, "Application"), " tab → ",
      el("strong", {}, "Cookies"), " → click your site.", el("br"),
      "3. The easiest copy method: switch to the ", el("strong", {}, "Network"),
      " tab, reload the page, click any request to your site, and find the ",
      el("code", {}, "Cookie:"), " header in Request Headers — copy its full value.",
      el("br"), el("br"),
      "Paste it below. Stored encrypted at rest, sent on every fetch.",
    ),
    el("label", {}, "Cookie string", cookieT),
    el("div", { class: "help" },
      data.has_cookies ? "✓ Cookies currently stored." : "No cookies stored."),
  );
  const renderCookies = () => { cookieBody.style.display = cookieEnable.checked ? "" : "none"; };
  cookieEnable.addEventListener("change", renderCookies);
  renderCookies();

  const collectCookies = () => ({
    enabled: cookieEnable.checked,
    hasNew: !!(cookieEnable.checked && cookieT.value.trim()),
    payload: cookieT.value.trim() ? { raw: cookieT.value.trim() } : null,
  });

  return {
    collect,
    collectAuth,
    collectCookies,
    collectNotify,
    elements: [
      el("label", {}, "Name", nameI),
      el("label", {}, "Description", descI),
      el("label", {}, "URLs (one per line)", urlsT),
      el("label", {}, "Extractors"),
      extractorsBox,
      el("button", { type: "button", onclick: () => addExtractorRow() }, "+ Add extractor"),
      el("label", {},
        zipI, " Zip parallel lists into records",
        el("div", { class: "help" },
          "Check this when your extractors return one list per table column. Output becomes ",
          el("code", {}, "[{col1: val, col2: val, ...}, ...]"),
          " instead of separate parallel arrays."),
      ),
      el("label", {},
        "Sort records by (optional, requires Zip)",
        el("div", { class: "row" }, sortFieldI, sortDirSel),
        el("label", { class: "muted", style: "margin-top: 6px;" },
          sortNumericI, " Treat as number (strips $, commas, %)"),
        el("div", { class: "help" },
          "Type the name of one of your extractors. e.g. ", el("code", {}, "premium"),
          " sorts the records list by that column. Leave blank for no sort."),
      ),
      el("label", {}, "Schedule type", schedSel),
      schedConfig,
      el("hr"),
      el("label", {}, notifyEnable, " Send Discord notification on each run"),
      notifyBody,
      el("hr"),
      el("label", {}, cookieEnable, " Send cookies on every fetch (for sites with JS-based login)"),
      cookieBody,
      el("hr"),
      el("label", {}, authEnable, " Site requires login (standard form POST)"),
      authBody,
    ],
  };
}

async function viewNew() {
  root.replaceChildren(el("h2", {}, "New job"));
  const { collect, collectAuth, collectCookies, collectNotify, elements } = jobFormFields(null);
  const form = el("form", { onsubmit: async (e) => {
    e.preventDefault();
    try {
      const payload = collect();
      const a = collectAuth();
      if (a.enabled) {
        if (!a.credentialsEntered) {
          banner("Enter a username and password — required when login is enabled.");
          return;
        }
        payload.auth = a.auth;
        payload.credentials = a.credentials;
      }
      const ck = collectCookies();
      if (ck.enabled) {
        if (!ck.hasNew) {
          banner("Paste a cookie string before saving — required when cookie auth is enabled.");
          return;
        }
        payload.cookies = ck.payload;
      }
      const nf = collectNotify();
      if (nf.enabled) {
        if (!nf.payload) {
          banner("Paste a Discord webhook URL before saving — required when notifications are enabled.");
          return;
        }
        payload.notify = nf.payload;
      }
      const job = await Jobs.create(payload);
      window.location.hash = `#/jobs/${job.id}`;
    } catch (err) { banner(err.message); }
  }}, ...elements,
    el("div", { class: "row", style: "margin-top: 20px;" },
      el("button", { type: "submit", class: "primary" }, "Create job"),
      el("a", { class: "btn", href: "#/jobs" }, "Cancel"),
    ),
  );
  root.appendChild(form);
}

async function viewEdit(id) {
  let job;
  try { job = await Jobs.get(id); }
  catch (e) { banner(e.message); return; }
  root.replaceChildren(el("h2", {}, `Edit: ${job.name}`));
  const { collect, collectAuth, collectCookies, collectNotify, elements } = jobFormFields(job);
  const form = el("form", { onsubmit: async (e) => {
    e.preventDefault();
    try {
      const payload = collect();
      const a = collectAuth();
      if (a.enabled) {
        payload.auth = a.auth;
        // Block: enabling auth with no stored creds AND no new creds entered.
        if (!job.has_credentials && !a.credentialsEntered) {
          banner("Enter a username and password — required when login is enabled.");
          return;
        }
      } else if (job.auth) {
        payload.clear_auth = true;
      }
      const nf = collectNotify();
      if (nf.enabled) {
        if (!nf.payload) {
          banner("Paste a Discord webhook URL — required when notifications are enabled.");
          return;
        }
        payload.notify = nf.payload;
      } else if (job.notify) {
        payload.clear_notify = true;
      }
      await Jobs.update(id, payload);
      if (a.enabled && a.credentialsEntered) {
        await Jobs.setCredentials(id, a.credentials);
      }
      const ck = collectCookies();
      if (ck.enabled && ck.hasNew) {
        await Jobs.setCookies(id, ck.payload);
      } else if (!ck.enabled && job.has_cookies) {
        await Jobs.clearCookies(id);
      }
      window.location.hash = `#/jobs/${id}`;
    } catch (err) { banner(err.message); }
  }}, ...elements,
    el("div", { class: "row", style: "margin-top: 20px;" },
      el("button", { type: "submit", class: "primary" }, "Save"),
      el("a", { class: "btn", href: `#/jobs/${id}` }, "Cancel"),
    ),
  );
  root.appendChild(form);
}

let detailPollTimer = null;
function stopDetailPoll() {
  if (detailPollTimer) { clearInterval(detailPollTimer); detailPollTimer = null; }
}

async function viewDetail(id) {
  stopDetailPoll();
  // Per-view: track which run rows are expanded so the 5s poll doesn't snap
  // them closed underneath the user.
  const expandedRuns = new Set();
  let job, runs;
  try {
    [job, runs] = await Promise.all([Jobs.get(id), Jobs.runs(id)]);
  } catch (e) { banner(e.message); return; }

  root.replaceChildren();

  const toolbar = el("div", { class: "toolbar" },
    el("div", {},
      el("h2", { style: "margin:0;" }, job.name),
      job.description ? el("div", { class: "muted" }, job.description) : null,
    ),
    el("div", { class: "actions" },
      el("button", { onclick: async () => { try { await Jobs.run(id); banner("Run triggered"); } catch (e) { banner(e.message); } } }, "Run now"),
      job.status === "paused"
        ? el("button", { onclick: async () => { try { await Jobs.resume(id); viewDetail(id); } catch (e) { banner(e.message); } } }, "Resume")
        : el("button", { onclick: async () => { try { await Jobs.pause(id);  viewDetail(id); } catch (e) { banner(e.message); } } }, "Pause"),
      el("a", { class: "btn", href: `#/jobs/${id}/edit` }, "Edit"),
      el("button", { class: "danger", onclick: async () => {
        if (!confirm(`Delete "${job.name}"?`)) return;
        try { await Jobs.remove(id); window.location.hash = "#/jobs"; } catch (e) { banner(e.message); }
      }}, "Delete"),
    ),
  );
  root.appendChild(toolbar);

  const metaContainer = el("div");
  root.appendChild(metaContainer);

  const runsHeader = el("h3");
  const refreshIndicator = el("span", { class: "refresh-dot", title: "Auto-refreshing every 5s" });
  const runsTitle = el("span");
  runsHeader.append(runsTitle, " ", refreshIndicator);
  root.appendChild(runsHeader);
  const runsContainer = el("div");
  root.appendChild(runsContainer);

  const renderMeta = (j) => {
    const authParts = [];
    if (j.auth) {
      authParts.push(`Form login at ${j.auth.login_url} (${j.auth.method.toUpperCase()}) — credentials ${j.has_credentials ? "stored" : "MISSING"}`);
    }
    if (j.has_cookies) authParts.push("Sending stored cookies on every fetch");
    const authSummary = authParts.length ? authParts.join("; ") : "None";
    const sortSummary = j.sort
      ? `${j.sort.field} ${j.sort.direction === "desc" ? "↓ (largest first)" : "↑ (smallest first)"}${j.sort.numeric ? " — numeric" : " — text"}`
      : "None";
    metaContainer.replaceChildren(el("table", {},
      el("tbody", {},
        el("tr", {}, el("th", {}, "Status"),    el("td", {}, el("span", { class: `badge ${j.status}` }, j.status))),
        el("tr", {}, el("th", {}, "Schedule"),  el("td", {}, humanSchedule(j))),
        el("tr", {}, el("th", {}, "URLs"),      el("td", {}, el("pre", { class: "mono" }, j.urls.join("\n")))),
        el("tr", {}, el("th", {}, "Extractors"), el("td", {}, el("pre", { class: "mono" }, JSON.stringify(j.extractors, null, 2)))),
        el("tr", {}, el("th", {}, "Sort"),      el("td", {}, sortSummary)),
        el("tr", {}, el("th", {}, "Notifications"),
          el("td", {}, j.notify
            ? `Discord — ${[
                j.notify.on_success ? "on success" : null,
                j.notify.on_error ? "on error" : null,
                j.notify.include_top_n ? `top ${j.notify.include_top_n}` : null,
                (j.notify.alert_field && j.notify.alert_threshold != null)
                  ? `alert ${j.notify.alert_field} ≥ ${j.notify.alert_threshold.toLocaleString()}`
                  : null,
              ].filter(Boolean).join(", ") || "disabled"}`
            : "None")),
        el("tr", {}, el("th", {}, "Authentication"), el("td", {}, authSummary)),
        el("tr", {}, el("th", {}, "Last run"),  el("td", {}, fmtDate(j.last_run_at))),
        el("tr", {}, el("th", {}, "Next run"),  el("td", {}, fmtDate(j.next_run_at))),
      ),
    ));
  };

  const renderRuns = (r) => {
    runsTitle.textContent = `Run history (${r.total})`;
    if (r.items.length === 0) {
      runsContainer.replaceChildren(el("p", { class: "muted" }, "No runs yet."));
      return;
    }
    runsContainer.replaceChildren(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Started"),
        el("th", {}, "Status"),
        el("th", {}, "Duration"),
        el("th", {}, "Details"),
      )),
      el("tbody", {}, ...r.items.map(run => runRow(run, expandedRuns))),
    ));
  };

  renderMeta(job);
  renderRuns(runs);

  detailPollTimer = setInterval(async () => {
    if (window.location.hash !== `#/jobs/${id}`) { stopDetailPoll(); return; }
    try {
      const [j, r] = await Promise.all([Jobs.get(id), Jobs.runs(id)]);
      renderMeta(j);
      renderRuns(r);
    } catch {
      // Silent — transient network errors shouldn't disturb the page.
    }
  }, 5000);
}

function runRow(run, expandedRuns) {
  const summary = run.error
    ? `error: ${run.error.split("\n")[0]}`
    : run.results.map(r => `${r.ok ? "✓" : "✗"} ${r.url}`).join("\n");
  const details = el("details", {},
    el("summary", { class: "mono" }, summary.split("\n")[0]),
    el("pre", {}, JSON.stringify({ results: run.results, error: run.error }, null, 2)),
  );
  if (expandedRuns && expandedRuns.has(run.id)) details.open = true;
  if (expandedRuns) {
    details.addEventListener("toggle", () => {
      if (details.open) expandedRuns.add(run.id);
      else expandedRuns.delete(run.id);
    });
  }
  return el("tr", {},
    el("td", {}, fmtDate(run.started_at)),
    el("td", {}, el("span", { class: `badge ${run.status}` }, run.status)),
    el("td", {}, fmtDuration(run.duration_ms)),
    el("td", {}, details),
  );
}

// --- router ---------------------------------------------------------------

function route() {
  // Always stop the detail poller on navigation; viewDetail re-arms it.
  stopDetailPoll();
  const h = window.location.hash || "#/jobs";
  const m = h.match(/^#\/jobs\/(\d+)\/edit$/);
  if (m) return viewEdit(+m[1]);
  const m2 = h.match(/^#\/jobs\/(\d+)$/);
  if (m2) return viewDetail(+m2[1]);
  if (h === "#/jobs/new") return viewNew();
  return viewList();
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
