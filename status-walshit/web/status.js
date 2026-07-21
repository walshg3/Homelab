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
    requireCondition(Object.hasOwn(EVENT_STATUSES, event.kind));
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
  }

  function bootstrap(windowObject) {
    const {document, fetch, setInterval} = windowObject;
    let requestGeneration = 0;
    let lastSuccessfulFingerprint = null;

    async function loadStatus() {
      const generation = ++requestGeneration;
      const controller = new windowObject.AbortController();
      const timeoutId = windowObject.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
      const requestOptions = {cache: "no-store", signal: controller.signal};
      try {
        const responses = await Promise.all([
          fetch(SUMMARY_PATH, requestOptions),
          fetch(HISTORY_PATH, requestOptions),
        ]);
        requireCondition(responses.every((response) => response.ok));
        const [summary, history] = await Promise.all(responses.map((response) => response.json()));
        const nowMs = Number.isFinite(windowObject.__STATUS_NOW_MS__)
          ? windowObject.__STATUS_NOW_MS__
          : Date.now();
        const model = buildViewModel(summary, history, nowMs);
        if (generation !== requestGeneration) return;
        const fingerprint = fingerprintViewModel(model);
        if (fingerprint === lastSuccessfulFingerprint) return;
        renderStatus(document, model);
        lastSuccessfulFingerprint = fingerprint;
      } catch (_error) {
        if (generation !== requestGeneration) return;
        lastSuccessfulFingerprint = null;
        renderUnavailable(document);
      } finally {
        windowObject.clearTimeout(timeoutId);
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
    buildViewModel,
    fingerprintViewModel,
    renderStatus,
    renderUnavailable,
    bootstrap,
  });
});
