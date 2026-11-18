(function exposeStatusRenderer(root, factory) {
  "use strict";

  const renderer = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = renderer;
  }
  root.StatusRenderer = renderer;
  if (root.document) renderer.bootstrap(root);
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  const SUMMARY_PATH = "/status/v2/summary.json";
  const HISTORY_PATH = "/status/v2/history.json";
  const INCIDENTS_PATH = "/status/v2/incidents.json";
  const REFRESH_INTERVAL_MS = 60_000;
  const FETCH_TIMEOUT_MS = 10_000;
  const STALE_AFTER_MS = 180_000;
  const COMPONENT_REGISTRY = Object.freeze([
    Object.freeze({id: "watch", name: "Watch"}),
    Object.freeze({id: "home", name: "Home base"}),
    Object.freeze({id: "requests", name: "Requests"}),
    Object.freeze({id: "audiobooks", name: "Audiobooks"}),
    Object.freeze({id: "site", name: "Walshit.com"}),
  ]);
  const PUBLIC_STATES = new Set(["operational", "degraded", "outage", "maintenance", "no-data"]);
  const HISTORY_STATES = new Set(PUBLIC_STATES);
  const EVENT_STATUSES = Object.freeze({
    incident: new Set(["investigating", "identified", "monitoring"]),
    maintenance: new Set(["planned", "in-progress"]),
  });
  const CLOSED_EVENT_STATUSES = Object.freeze({incident: "resolved", maintenance: "completed"});
  const STATE_PRESENTATIONS = Object.freeze({
    operational: Object.freeze({label: "Operational", className: "state-operational"}),
    degraded: Object.freeze({label: "Degraded", className: "state-degraded"}),
    outage: Object.freeze({label: "Outage", className: "state-outage"}),
    maintenance: Object.freeze({label: "Maintenance", className: "state-maintenance"}),
    "no-data": Object.freeze({label: "No data", className: "state-no-data"}),
  });

  function isStale(generatedAt, nowMs) {
    return nowMs - Date.parse(generatedAt) > STALE_AFTER_MS;
  }

  function requireCondition(condition) {
    if (!condition) throw new TypeError("Invalid public status data");
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function hasExactKeys(value, keys) {
    return isObject(value) && Object.keys(value).sort().join("|") === [...keys].sort().join("|");
  }

  function isText(value) {
    return typeof value === "string" && value.trim().length > 0;
  }

  function isTimestamp(value) {
    if (typeof value !== "string") return false;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
    if (!match || !isDate(match[1])) return false;
    const [, , hour, minute, second, offsetHour="0", offsetMinute="0"] = match;
    return Number(hour) <= 23 && Number(minute) <= 59 && Number(second) <= 59 &&
      Number(offsetHour) <= 23 && Number(offsetMinute) <= 59 && Number.isFinite(Date.parse(value));
  }

  function timestampMicros(value) {
    const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    requireCondition(match && isTimestamp(value));
    return BigInt(Date.parse(match[1] + match[3])) * 1000n +
      BigInt((match[2] || "").slice(0, 6).padEnd(6, "0"));
  }

  function isDate(value) {
    if (typeof value !== "string" || value.startsWith("0000-") ||
      !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
  }

  function registryName(componentId) {
    return COMPONENT_REGISTRY.find((entry) => entry.id === componentId)?.name;
  }

  function validateEvent(event) {
    requireCondition(hasExactKeys(event, [
      "id", "kind", "status", "title", "body", "started_at", "updated_at",
      "affected_components", "updates",
    ]));
    requireCondition(typeof event.id === "string" && /^[a-z0-9][a-z0-9-]{2,63}$/.test(event.id));
    requireCondition(typeof event.kind === "string" && Object.hasOwn(EVENT_STATUSES, event.kind));
    requireCondition(EVENT_STATUSES[event.kind].has(event.status));
    requireCondition(isText(event.title) && isText(event.body));
    requireCondition(isTimestamp(event.started_at) && isTimestamp(event.updated_at));
    const startedAt = Date.parse(event.started_at);
    const updatedAt = Date.parse(event.updated_at);
    requireCondition(updatedAt >= startedAt);
    requireCondition(Array.isArray(event.affected_components) && event.affected_components.length > 0);
    requireCondition(new Set(event.affected_components).size === event.affected_components.length);
    requireCondition(event.affected_components.every((id) => registryName(id)));
    requireCondition(Array.isArray(event.updates) && event.updates.length > 0);
    let previousUpdate = -Infinity;
    event.updates.forEach((update) => {
      requireCondition(hasExactKeys(update, ["at", "status", "body"]));
      requireCondition(isTimestamp(update.at) && EVENT_STATUSES[event.kind].has(update.status));
      requireCondition(isText(update.body));
      const updateAt = Date.parse(update.at);
      requireCondition(updateAt >= startedAt && updateAt <= updatedAt && updateAt > previousUpdate);
      previousUpdate = updateAt;
    });
    requireCondition(previousUpdate === updatedAt);
  }

  function validateSummary(summary) {
    requireCondition(hasExactKeys(summary, [
      "schema_version", "generated_at", "overall", "components", "active_events",
    ]));
    requireCondition(summary.schema_version === "2" && isTimestamp(summary.generated_at));
    requireCondition(hasExactKeys(summary.overall, ["state", "headline", "body"]));
    requireCondition(PUBLIC_STATES.has(summary.overall.state));
    requireCondition(isText(summary.overall.headline) && isText(summary.overall.body));
    requireCondition(Array.isArray(summary.components) && summary.components.length === COMPONENT_REGISTRY.length);
    const ids = [];
    summary.components.forEach((component) => {
      requireCondition(hasExactKeys(component, [
        "id", "name", "description", "state", "availability_90d_pct",
        "availability_seconds_measured", "history_days_available",
      ]));
      requireCondition(registryName(component.id) === component.name && isText(component.description));
      requireCondition(PUBLIC_STATES.has(component.state));
      requireCondition(component.availability_90d_pct === null ||
        (typeof component.availability_90d_pct === "number" &&
          Number.isFinite(component.availability_90d_pct) &&
          component.availability_90d_pct >= 0 && component.availability_90d_pct <= 100));
      requireCondition(component.availability_90d_pct === null || Math.abs(
        (component.availability_90d_pct * 1000) -
        Math.round(component.availability_90d_pct * 1000)
      ) < 1e-9);
      requireCondition(Number.isInteger(component.availability_seconds_measured) &&
        component.availability_seconds_measured >= 0 &&
        component.availability_seconds_measured <= 7_776_000);
      requireCondition((component.availability_90d_pct === null) ===
        (component.availability_seconds_measured === 0));
      requireCondition(Number.isInteger(component.history_days_available) &&
        component.history_days_available >= 0 && component.history_days_available <= 90);
      ids.push(component.id);
    });
    requireCondition(new Set(ids).size === COMPONENT_REGISTRY.length &&
      COMPONENT_REGISTRY.every((entry) => ids.includes(entry.id)));
    requireCondition(Array.isArray(summary.active_events));
    summary.active_events.forEach(validateEvent);
    const eventIds = summary.active_events.map((event) => event.id);
    requireCondition(new Set(eventIds).size === eventIds.length);
    return summary;
  }

  function validateHistory(history) {
    requireCondition(hasExactKeys(history, ["schema_version", "generated_at", "components"]));
    requireCondition(history.schema_version === "2" && isTimestamp(history.generated_at));
    requireCondition(Array.isArray(history.components) && history.components.length === COMPONENT_REGISTRY.length);
    const ids = [];
    history.components.forEach((component) => {
      requireCondition(hasExactKeys(component, ["id", "name", "days"]));
      requireCondition(registryName(component.id) === component.name);
      requireCondition(Array.isArray(component.days) && component.days.length <= 90);
      let previousDate = "";
      component.days.forEach((day) => {
        requireCondition(hasExactKeys(day, ["date", "state"]));
        requireCondition(isDate(day.date) && HISTORY_STATES.has(day.state) && day.date > previousDate);
        previousDate = day.date;
      });
      ids.push(component.id);
    });
    requireCondition(new Set(ids).size === COMPONENT_REGISTRY.length &&
      COMPONENT_REGISTRY.every((entry) => ids.includes(entry.id)));
    return history;
  }

  function isPublicNarrative(value) {
    if (!isText(value)) return false;
    if (/(?:[a-z][a-z0-9+.-]*:\/\/|(?:https?|ftp|file|ssh|mailto|data|javascript):)/i.test(value)) return false;
    if (/\blocal[h]ost\b/i.test(value)) return false;
    if (/\b(?:api[_-]?key|client[_-]?secret|secret|password|passphrase|credentials?|authorization|(?:access|refresh)[_-]?tokens?|tokens?)\b\s*[:=]/i.test(value)) return false;
    if (/\b(?:bearer|basic)\s+[a-z0-9+/._=-]{16,}/i.test(value)) return false;
    if (/-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b/.test(value)) return false;
    if (/(?:^|[\s"'=:(])\/(?:home|root|srv|mnt|opt|etc|var\/lib|usr\/local|tmp)(?:\/|\b)/i.test(value)) return false;
    if (/(?:^|[^a-z0-9])[a-z]:\\|\\\\[a-z0-9._-]+\\/i.test(value)) return false;
    if (/\b(?:media|movie|series|episode|book|library)[_-]?(?:title|name)\s*[:=]|\b(?:now[_-]?playing|current[_-]?(?:media|movie|series|episode|book)|library[_-]?item)\s*[:=]/i.test(value)) return false;
    if (/\b(?:operator|owner|admin|person|full)[_-]?name\s*[:=]|\b(?:contact[_-]?person|personal[_-]?identity)\s*[:=]/i.test(value)) return false;
    if (/\b(?:operator|owner|admin|administrator)\s+[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+)+\b/.test(value)) return false;
    if (/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i.test(value)) return false;
    if (/\b(?:recovery|restore|failover|backup)(?:[_-]|\s+)(?:key|code|path|command|host|target|procedure|steps?|instructions?|details?)\b/i.test(value)) return false;
    if (/\b(?:10|127|192\.168|169\.254)\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\b|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b/i.test(value)) return false;
    const ipv6Candidates = value.match(/(?<![0-9a-z:.])(?:[0-9a-f]{0,4}:){2,}[0-9a-f]{0,4}(?![0-9a-z:])/gi) || [];
    if (ipv6Candidates.some(isRestrictedIpv6)) return false;
    const hostnames = value.match(/(?:[a-z0-9-]+\.)+[a-z]{2,}/gi) || [];
    if (hostnames.some((host) => host.toLowerCase() !== "walshit.com")) return false;
    if (/(?:\/\/|\\|\.)walshit\.com|walshit\.com(?:[\/?#:\\]|%(?:2f|3f|23|3a|5c))/i.test(value)) return false;
    return true;
  }

  function isRestrictedIpv6(candidate) {
    const parts = candidate.split("::");
    if (parts.length > 2) return false;
    const left = parts[0] ? parts[0].split(":") : [];
    const right = parts.length === 2 && parts[1] ? parts[1].split(":") : [];
    const supplied = [...left, ...right];
    if (supplied.some((part) => !/^[0-9a-f]{1,4}$/i.test(part))) return false;
    if (parts.length === 1 && supplied.length !== 8) return false;
    if (parts.length === 2 && supplied.length >= 8) return false;
    const groups = [...left, ...Array(parts.length === 2 ? 8 - supplied.length : 0).fill("0"), ...right]
      .map((part) => parseInt(part, 16));
    return (groups[0] >= 0xfc00 && groups[0] <= 0xfdff) ||
      (groups[0] >= 0xfe80 && groups[0] <= 0xfebf) ||
      (groups.slice(0, 7).every((part) => part === 0) && groups[7] === 1);
  }

  function validateIncidents(incidents, summaryGeneratedAt, nowMs) {
    requireCondition(hasExactKeys(incidents, ["schema_version", "generated_at", "events"]));
    requireCondition(incidents.schema_version === "2" && isTimestamp(incidents.generated_at));
    const summary = isObject(summaryGeneratedAt) ? summaryGeneratedAt : null;
    const summaryTime = summary ? summary.generated_at : summaryGeneratedAt;
    if (summaryTime !== undefined) requireCondition(isTimestamp(summaryTime) && incidents.generated_at === summaryTime);
    if (nowMs !== undefined) requireCondition(Number.isFinite(nowMs) &&
      timestampMicros(incidents.generated_at) <= BigInt(Math.trunc(nowMs * 1000)) &&
      BigInt(Math.trunc(nowMs * 1000)) - timestampMicros(incidents.generated_at) <= BigInt(STALE_AFTER_MS) * 1000n);
    requireCondition(Array.isArray(incidents.events));
    if (summary) requireCondition(Array.isArray(summary.active_events) &&
      !summary.active_events.some((active) => incidents.events.some((closed) => active.id === closed.id)));
    const ids = new Set();
    let previousResolved = null;
    let previousId = "";
    incidents.events.forEach((event) => {
      requireCondition(hasExactKeys(event, ["id", "kind", "status", "title", "body", "started_at", "updated_at", "resolved_at", "affected_components", "updates"]));
      requireCondition(typeof event.id === "string" && /^[a-z0-9][a-z0-9-]{2,63}$/.test(event.id) && !ids.has(event.id));
      ids.add(event.id);
      requireCondition(typeof event.kind === "string" && Object.hasOwn(CLOSED_EVENT_STATUSES, event.kind));
      requireCondition(event.status === CLOSED_EVENT_STATUSES[event.kind]);
      requireCondition(isPublicNarrative(event.title) && isPublicNarrative(event.body));
      requireCondition(isTimestamp(event.started_at) && isTimestamp(event.updated_at) && isTimestamp(event.resolved_at));
      const start = timestampMicros(event.started_at);
      const resolved = timestampMicros(event.resolved_at);
      requireCondition(start <= resolved && timestampMicros(event.updated_at) === resolved && resolved <= timestampMicros(incidents.generated_at));
      requireCondition(previousResolved === null || resolved < previousResolved || (resolved === previousResolved && event.id >= previousId));
      previousResolved = resolved;
      previousId = event.id;
      requireCondition(Array.isArray(event.affected_components) && event.affected_components.length > 0);
      requireCondition(new Set(event.affected_components).size === event.affected_components.length);
      requireCondition(event.affected_components.every((id) => registryName(id)));
      requireCondition(Array.isArray(event.updates) && event.updates.length > 0);
      let previousUpdate = null;
      event.updates.forEach((update, index) => {
        requireCondition(hasExactKeys(update, ["at", "status", "body"]));
        requireCondition(isTimestamp(update.at) && isPublicNarrative(update.body));
        requireCondition(index === event.updates.length - 1
          ? update.status === event.status : EVENT_STATUSES[event.kind].has(update.status));
        const at = timestampMicros(update.at);
        requireCondition(at >= start && at <= resolved && (previousUpdate === null || at > previousUpdate));
        previousUpdate = at;
      });
      requireCondition(previousUpdate === resolved);
    });
    return incidents;
  }

  function formatPercentage(value) {
    return String(value);
  }

  function formatMeasuredDuration(totalSeconds) {
    requireCondition(Number.isInteger(totalSeconds) && totalSeconds >= 0 && totalSeconds <= 7_776_000);
    const days = Math.floor(totalSeconds / 86_400);
    const hours = Math.floor((totalSeconds % 86_400) / 3_600);
    const minutes = Math.floor((totalSeconds % 3_600) / 60);
    const seconds = totalSeconds % 60;
    if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
    if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
    if (minutes > 0) return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
    return `${seconds}s`;
  }

  function calendarWindow(generatedAt) {
    const endMs = Date.parse(generatedAt);
    const dayMs = 24 * 60 * 60 * 1000;
    return Array.from({length: 90}, (_value, index) =>
      new Date(endMs - ((89 - index) * dayMs)).toISOString().slice(0, 10)
    );
  }

  function buildViewModel(summary, history, nowMs) {
    validateSummary(summary);
    validateHistory(history);
    requireCondition(summary.generated_at === history.generated_at);
    requireCondition(Number.isFinite(nowMs));
    const summaryById = new Map(summary.components.map((component) => [component.id, component]));
    const historyById = new Map(history.components.map((component) => [component.id, component]));
    const windowDates = calendarWindow(history.generated_at);
    const windowStart = windowDates[0];
    const windowEnd = windowDates.at(-1);

    const components = COMPONENT_REGISTRY.map(({id}) => {
      const component = summaryById.get(id);
      const componentHistory = historyById.get(id);
      requireCondition(component && componentHistory && component.name === componentHistory.name);
      requireCondition(componentHistory.days.every((day) =>
        day.date >= windowStart && day.date <= windowEnd
      ));
      const suppliedByDate = new Map(componentHistory.days.map((day) => [day.date, day]));
      const marks = windowDates.map((date) => {
        const supplied = suppliedByDate.get(date);
        return supplied ? {...supplied} : {date, state: "no-data"};
      });
      const measuredDays = marks.filter((day) => day.state !== "no-data").length;
      requireCondition(measuredDays === component.history_days_available);
      const latest = componentHistory.days.at(-1);
      const periodLabel = `${measuredDays} ${measuredDays === 1 ? "day" : "days"} on record`;
      return Object.freeze({
        ...component,
        presentation: STATE_PRESENTATIONS[component.state],
        marks,
        availabilityLabel: component.availability_90d_pct === null
          ? `${periodLabel} · measured availability unavailable`
          : `${formatPercentage(component.availability_90d_pct)}% measured availability · ` +
            `${formatMeasuredDuration(component.availability_seconds_measured)} measured · ${periodLabel}`,
        historySummary: `90-day availability strip for ${component.name}: ${measuredDays} ` +
          `${measuredDays === 1 ? "day" : "days"} with recorded states; newest recorded state ` +
          `${STATE_PRESENTATIONS[latest?.state || "no-data"].label}.`,
      });
    });
    const summaryStale = isStale(summary.generated_at, nowMs);
    const historyStale = isStale(history.generated_at, nowMs);
    return Object.freeze({
      overall: Object.freeze({...summary.overall, presentation: STATE_PRESENTATIONS[summary.overall.state]}),
      generatedAt: summary.generated_at,
      components,
      activeEvents: summary.active_events,
      stale: Object.freeze({summary: summaryStale, history: historyStale, any: summaryStale || historyStale}),
    });
  }

  function fingerprintViewModel(model) {
    const canonicalize = (value) => {
      if (Array.isArray(value)) return value.map(canonicalize);
      if (!isObject(value)) return value;
      return Object.fromEntries(
        Object.keys(value).sort().map((key) => [key, canonicalize(value[key])])
      );
    };
    return JSON.stringify(canonicalize(model));
  }

  function clearElement(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function createElement(document, tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatTimestamp(timestamp) {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(timestamp));
  }

  function createTime(document, timestamp) {
    const node = createElement(document, "time", "event-time", formatTimestamp(timestamp));
    node.setAttribute("datetime", timestamp);
    return node;
  }

  function setOverallState(document, presentation, headline, body, generatedAt) {
    const overall = document.querySelector(".overall");
    Object.values(STATE_PRESENTATIONS).forEach(({className}) => overall.classList.remove(className));
    overall.classList.add(presentation.className);
    document.getElementById("overall-state-label").textContent = presentation.label;
    document.getElementById("overall-title").textContent = headline;
    document.querySelector(".overall-copy").textContent = body;
    document.getElementById("generated-at").textContent = generatedAt
      ? `Generated ${formatTimestamp(generatedAt)}.`
      : "Generation time unavailable.";
  }

  function renderHistoryStrip(document, container, component) {
    clearElement(container);
    container.className = "history-placeholder history-strip-container";
    container.setAttribute("role", "img");
    container.setAttribute("aria-label", component.historySummary);
    const strip = createElement(document, "span", "history-strip");
    component.marks.forEach((mark) => {
      const presentation = STATE_PRESENTATIONS[mark.state];
      const item = createElement(document, "span", `history-mark ${presentation.className}`);
      item.setAttribute("aria-hidden", "true");
      item.setAttribute("data-history-state", mark.state);
      item.setAttribute("title", mark.date ? `${mark.date} — ${presentation.label}` : "No data");
      strip.appendChild(item);
    });
    container.appendChild(strip);
    container.appendChild(createElement(document, "span", "history-meta", component.availabilityLabel));
  }

  function renderComponent(document, component) {
    const row = document.querySelector(`[data-component="${component.id}"]`);
    requireCondition(row);
    row.querySelector("h3").textContent = component.name;
    row.querySelector(".component-name p").textContent = component.description;
    renderHistoryStrip(document, row.querySelector(".history-placeholder"), component);
    const state = row.querySelector(".current-state");
    clearElement(state);
    state.appendChild(createElement(
      document,
      "span",
      `state-stamp ${component.presentation.className}`,
      component.presentation.label,
    ));
  }

  function appendEventStub(document, ticket, label, code) {
    const stub = createElement(document, "div", "event-stub");
    stub.appendChild(createElement(document, "span", "eyebrow", label));
    stub.appendChild(createElement(document, "span", "event-code", code));
    ticket.appendChild(stub);
  }

  function renderQuietBoard(document, title, body, unavailable=false) {
    const board = document.getElementById("active-board");
    clearElement(board);
    const ticket = createElement(document, "article", "ticket-event event-quiet");
    if (unavailable) ticket.classList.add("event-unavailable");
    appendEventStub(document, ticket, unavailable ? "Board unverified" : "Quiet board", "No. —");
    const main = createElement(document, "div", "event-main");
    main.appendChild(createElement(document, "p", "eyebrow", unavailable ? "Current notices unavailable" : "No active events"));
    main.appendChild(createElement(document, "h3", "event-title", title));
    main.appendChild(createElement(document, "p", "event-body", body));
    ticket.appendChild(main);
    board.appendChild(ticket);
  }

  function renderEvent(document, board, event, summaryComponents, index) {
    const kindLabel = event.kind === "incident" ? "Incident" : "Maintenance";
    const statusLabel = event.status.replace("-", " ").replace(/^./, (letter) => letter.toUpperCase());
    const ticket = createElement(document, "article", `ticket-event active-event kind-${event.kind}`);
    const titleId = `active-event-title-${index + 1}`;
    ticket.setAttribute("aria-labelledby", titleId);
    appendEventStub(document, ticket, kindLabel, event.id);
    const main = createElement(document, "div", "event-main");
    main.appendChild(createElement(
      document,
      "p",
      "eyebrow event-status",
      `${statusLabel} · Severity not specified by v2 contract`,
    ));
    const title = createElement(document, "h3", "event-title", event.title);
    title.id = titleId;
    main.appendChild(title);
    main.appendChild(createElement(document, "p", "event-body", event.body));

    const metadata = createElement(document, "dl", "event-meta");
    const addMetadata = (term, value) => {
      metadata.appendChild(createElement(document, "dt", "event-meta-label", term));
      if (typeof value === "string") {
        metadata.appendChild(createElement(document, "dd", "event-meta-value", value));
      } else {
        const definition = createElement(document, "dd", "event-meta-value");
        definition.appendChild(value);
        metadata.appendChild(definition);
      }
    };
    addMetadata("Started", createTime(document, event.started_at));
    addMetadata("Updated", createTime(document, event.updated_at));
    const names = event.affected_components.map((id) =>
      summaryComponents.find((component) => component.id === id).name
    );
    addMetadata("Affected", names.join(", "));
    main.appendChild(metadata);

    const details = createElement(document, "details", "event-timeline");
    details.appendChild(createElement(
      document,
      "summary",
      "event-timeline-summary",
      `Update timeline (${event.updates.length})`,
    ));
    const updates = createElement(document, "ol", "event-updates");
    event.updates.forEach((update) => {
      const item = createElement(document, "li", "event-update");
      const heading = createElement(document, "div", "event-update-head");
      heading.appendChild(createTime(document, update.at));
      heading.appendChild(createElement(
        document,
        "span",
        "event-update-status",
        update.status.replace("-", " "),
      ));
      item.appendChild(heading);
      item.appendChild(createElement(document, "p", "event-update-body", update.body));
      updates.appendChild(item);
    });
    details.appendChild(updates);
    main.appendChild(details);
    ticket.appendChild(main);
    board.appendChild(ticket);
  }

  function renderEvents(document, events, components) {
    const boardLabel = document.querySelector(".active-section .section-head > p");
    if (events.length === 0) {
      boardLabel.textContent = "Board clear";
      renderQuietBoard(
        document,
        "Nothing active to report.",
        "No active incidents or planned maintenance are on the board.",
      );
      return;
    }
    boardLabel.textContent = `${events.length} active ${events.length === 1 ? "event" : "events"}`;
    const board = document.getElementById("active-board");
    clearElement(board);
    events.forEach((event, index) => renderEvent(document, board, event, components, index));
  }

  function renderArchiveMessage(document, heading, body, label) {
    const section = document.querySelector(".history-section");
    if (!section) return;
    section.querySelector(".section-head > p").textContent = label;
    const list = section.querySelector(".history-list");
    clearElement(list);
    const card = createElement(document, "article", "history-empty");
    card.appendChild(createElement(document, "p", "history-date", "—"));
    const copy = createElement(document, "div", "history-copy");
    copy.appendChild(createElement(document, "h3", "", heading));
    copy.appendChild(createElement(document, "p", "", body));
    card.appendChild(copy);
    list.appendChild(card);
  }

  function renderIncidents(document, incidents) {
    if (!incidents.events.length) {
      renderArchiveMessage(document, "No resolved incidents or completed maintenance.", "The incident archive contains no resolved incidents or completed maintenance.", "Archive empty");
      return;
    }
    const section = document.querySelector(".history-section");
    section.querySelector(".section-head > p").textContent = `${incidents.events.length} filed ${incidents.events.length === 1 ? "event" : "events"}`;
    const list = section.querySelector(".history-list");
    clearElement(list);
    incidents.events.forEach((event, index) => {
      const card = createElement(document, "article", `history-card kind-${event.kind}`);
      const titleId = `filed-event-title-${index + 1}`;
      card.setAttribute("aria-labelledby", titleId);
      const date = createElement(document, "p", "history-date");
      date.appendChild(createTime(document, event.resolved_at));
      card.appendChild(date);
      const copy = createElement(document, "div", "history-copy");
      copy.appendChild(createElement(document, "p", "eyebrow", `${event.kind} · ${event.status}`));
      const title = createElement(document, "h3", "", event.title);
      title.id = titleId;
      copy.appendChild(title);
      copy.appendChild(createElement(document, "p", "history-body", event.body));
      const meta = createElement(document, "dl", "history-meta");
      [["Started", createTime(document, event.started_at)], ["Resolved", createTime(document, event.resolved_at)],
        ["Affected", event.affected_components.map(registryName).join(", ")]].forEach(([term, value]) => {
        meta.appendChild(createElement(document, "dt", "", term));
        const dd = createElement(document, "dd");
        dd.appendChild(typeof value === "string" ? document.createTextNode(value) : value);
        meta.appendChild(dd);
      });
      copy.appendChild(meta);
      const details = createElement(document, "details", "history-timeline");
      details.appendChild(createElement(document, "summary", "", `Update timeline (${event.updates.length})`));
      const updates = createElement(document, "ol", "history-updates");
      event.updates.forEach((update) => {
        const item = createElement(document, "li", "history-update");
        item.appendChild(createTime(document, update.at));
        item.appendChild(createElement(document, "span", "history-update-status", update.status.replace("-", " ")));
        item.appendChild(createElement(document, "p", "", update.body));
        updates.appendChild(item);
      });
      details.appendChild(updates);
      copy.appendChild(details);
      card.appendChild(copy);
      list.appendChild(card);
    });
  }

  function renderArchiveUnavailable(document) {
    renderArchiveMessage(document, "Incident archive unavailable.", "Filed incident data could not be verified. Please try again later.", "Archive unavailable");
  }

  function renderStatus(document, model) {
    document.body.setAttribute("data-status-render", "ready");
    document.getElementById("load-error").hidden = true;
    const staleWarning = document.getElementById("stale-warning");
    staleWarning.hidden = !model.stale.any;
    if (model.stale.any) {
      const sources = model.stale.summary && model.stale.history
        ? "Summary and history data are"
        : `${model.stale.summary ? "Summary" : "History"} data is`;
      staleWarning.textContent = `${sources} stale. Current conditions are unverified.`;
    }
    setOverallState(
      document,
      model.overall.presentation,
      model.overall.headline,
      model.overall.body,
      model.generatedAt,
    );
    model.components.forEach((component) => renderComponent(document, component));
    renderEvents(document, model.activeEvents, model.components);
  }

  function unavailableComponent(id, name, description) {
    return {
      id,
      name,
      description,
      presentation: STATE_PRESENTATIONS["no-data"],
      marks: Array.from({length: 90}, () => ({date: null, state: "no-data"})),
      availabilityLabel: "Measured availability unavailable",
      historySummary: `90-day availability strip for ${name}: data unavailable.`,
    };
  }

  function renderUnavailable(document) {
    document.body.setAttribute("data-status-render", "unavailable");
    const error = document.getElementById("load-error");
    error.textContent = "Status data is unavailable and current conditions cannot be verified.";
    error.hidden = false;
    document.getElementById("stale-warning").hidden = true;
    setOverallState(
      document,
      STATE_PRESENTATIONS["no-data"],
      "Status unavailable.",
      "Current conditions could not be verified. Please try again later.",
      null,
    );
    COMPONENT_REGISTRY.forEach(({id, name}) => {
      const row = document.querySelector(`[data-component="${id}"]`);
      const description = row.querySelector(".component-name p").textContent;
      renderComponent(document, unavailableComponent(id, name, description));
    });
    document.querySelector(".active-section .section-head > p").textContent = "Board unverified";
    renderQuietBoard(
      document,
      "Active-event data unavailable.",
      "No claim about current incidents or maintenance can be made.",
      true,
    );
    renderArchiveUnavailable(document);
  }

  async function fetchJsonWithDeadline(windowObject, path) {
      const controller = new windowObject.AbortController();
      let timeoutId;
      const deadline = new Promise((_resolve, reject) => {
        timeoutId = windowObject.setTimeout(() => {
          controller.abort();
          reject(new Error("Status request timed out"));
        }, FETCH_TIMEOUT_MS);
      });
      try {
        return await Promise.race([
          (async () => {
            const response = await windowObject.fetch(path, {cache: "no-store", signal: controller.signal});
            requireCondition(response.ok);
            return response.json();
          })(),
          deadline,
        ]);
      } finally {
        windowObject.clearTimeout(timeoutId);
      }
  }

  function bootstrap(windowObject) {
    const {document, setInterval} = windowObject;
    let requestGeneration = 0;
    let lastSuccessfulFingerprint = null;
    let lastArchiveFingerprint = null;
    let displayedArchive = null;

    function showArchiveLoading() {
      displayedArchive = null;
      lastArchiveFingerprint = null;
      renderArchiveMessage(document, "Loading incident archive.", "Filed incidents and completed maintenance are being checked.", "Archive loading");
    }

    async function loadStatus() {
      const generation = ++requestGeneration;
      let summaryDocument = null;
      const startingNowMs = Number.isFinite(windowObject.__STATUS_NOW_MS__)
        ? windowObject.__STATUS_NOW_MS__ : Date.now();
      if (!displayedArchive || timestampMicros(displayedArchive.generated_at) > BigInt(Math.trunc(startingNowMs * 1000)) ||
          BigInt(Math.trunc(startingNowMs * 1000)) - timestampMicros(displayedArchive.generated_at) > BigInt(STALE_AFTER_MS) * 1000n) {
        showArchiveLoading();
      }
      const archiveResult = fetchJsonWithDeadline(windowObject, INCIDENTS_PATH)
        .then((incidents) => ({incidents}), () => ({error: true}));
      try {
        const [summary, history] = await Promise.all([
          fetchJsonWithDeadline(windowObject, SUMMARY_PATH),
          fetchJsonWithDeadline(windowObject, HISTORY_PATH),
        ]);
        const nowMs = Number.isFinite(windowObject.__STATUS_NOW_MS__)
          ? windowObject.__STATUS_NOW_MS__
          : Date.now();
        const model = buildViewModel(summary, history, nowMs);
        if (generation !== requestGeneration) return;
        summaryDocument = summary;
        if (displayedArchive && displayedArchive.generated_at !== summary.generated_at) showArchiveLoading();
        const fingerprint = fingerprintViewModel(model);
        if (fingerprint !== lastSuccessfulFingerprint) renderStatus(document, model);
        lastSuccessfulFingerprint = fingerprint;
      } catch (_error) {
        if (generation !== requestGeneration) return;
        lastSuccessfulFingerprint = null;
        renderUnavailable(document);
        displayedArchive = null;
        lastArchiveFingerprint = null;
      }
      try {
        const result = await archiveResult;
        requireCondition(!result.error);
        const {incidents} = result;
        const nowMs = Number.isFinite(windowObject.__STATUS_NOW_MS__) ? windowObject.__STATUS_NOW_MS__ : Date.now();
        requireCondition(summaryDocument !== null);
        validateIncidents(incidents, summaryDocument, nowMs);
        if (generation !== requestGeneration) return;
        const fingerprint = JSON.stringify(incidents);
        if (fingerprint !== lastArchiveFingerprint) renderIncidents(document, incidents);
        lastArchiveFingerprint = fingerprint;
        displayedArchive = incidents;
      } catch (_error) {
        if (generation !== requestGeneration) return;
        lastArchiveFingerprint = null;
        displayedArchive = null;
        renderArchiveUnavailable(document);
      }
    }

    function start() {
      loadStatus();
      setInterval(loadStatus, REFRESH_INTERVAL_MS);
    }

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start);
    } else {
      start();
    }
  }

  return Object.freeze({
    COMPONENT_REGISTRY,
    STATE_PRESENTATIONS,
    isStale,
    validateSummary,
    validateHistory,
    validateIncidents,
    buildViewModel,
    fingerprintViewModel,
    renderStatus,
    renderUnavailable,
    renderIncidents,
    fetchJsonWithDeadline,
    bootstrap,
  });
});
