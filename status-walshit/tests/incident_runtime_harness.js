"use strict";

const renderer = require("../web/status.js");
const archive = require("../fixtures/incidents.ups-maintenance.json");
const summary = structuredClone(require("../fixtures/summary.operational.json"));
const history = structuredClone(require("../fixtures/history.sample.json"));
summary.generated_at = archive.generated_at;
history.generated_at = archive.generated_at;
for (const component of summary.components) component.history_days_available = 0;
for (const component of history.components) component.days = [];

class Node {
  constructor() {
    this.children = [];
    this.queries = new Map();
    this.attrs = {};
    this.hidden = false;
    this.value = "";
    this.classList = {add() {}, remove() {}};
  }
  get firstChild() { return this.children[0]; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(key, value) { this.attrs[key] = value; }
  set textContent(value) { this.value = value; this.children = []; }
  get textContent() { return String(this.value) + this.children.map((child) => child.textContent).join(""); }
  querySelector(selector) {
    if (!this.queries.has(selector)) this.queries.set(selector, new Node());
    return this.queries.get(selector);
  }
}

const document = new Node();
document.body = new Node();
document.readyState = "complete";
document.ids = new Map();
document.getElementById = (id) => {
  if (!document.ids.has(id)) document.ids.set(id, new Node());
  return document.ids.get(id);
};
document.createElement = () => new Node();
document.createTextNode = (text) => { const node = new Node(); node.textContent = text; return node; };

let refresh;
let currentArchive = archive;
let pendingArchive;
let archiveError = false;
const fetch = (path, options) => {
  if (!options.signal || options.cache !== "no-store") throw Error("request options missing");
  if (path.endsWith("summary.json")) return Promise.resolve({ok: true, json: async () => summary});
  if (path.endsWith("history.json")) return Promise.resolve({ok: true, json: async () => history});
  if (archiveError) return Promise.resolve({ok: false});
  if (pendingArchive) return pendingArchive.promise;
  return Promise.resolve({ok: true, json: async () => currentArchive});
};
const windowObject = {
  document, fetch, AbortController, setTimeout, clearTimeout,
  setInterval(callback) { refresh = callback; },
  __STATUS_NOW_MS__: Date.parse(archive.generated_at),
};
const archiveText = () => document.querySelector(".history-section").querySelector(".history-list").textContent;
const state = () => document.body.attrs["data-status-render"];
const tick = () => new Promise((resolve) => setImmediate(resolve));
const later = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
};

(async () => {
  renderer.bootstrap(windowObject);
  await tick();
  const loaded = state() === "ready" && archiveText().includes(archive.events[0].title);
  const card = document.querySelector(".history-section").querySelector(".history-list").children[0];
  const timeline = card.children[1].children.at(-1);
  timeline.open = true;
  pendingArchive = later();
  refresh();
  await tick();
  const retained = state() === "ready" && archiveText().includes(archive.events[0].title) &&
    document.querySelector(".history-section").querySelector(".history-list").children[0] === card && timeline.open;
  pendingArchive.resolve({ok: true, json: async () => archive});
  await tick();
  const unchanged = document.querySelector(".history-section").querySelector(".history-list").children[0] === card && timeline.open;
  pendingArchive = later();
  refresh();
  await tick();
  const old = pendingArchive;
  pendingArchive = null;
  summary.generated_at = "2026-09-26T23:27:00Z";
  history.generated_at = summary.generated_at;
  pendingArchive = later();
  refresh();
  await tick();
  const newGenerationPending = archiveText().includes("Loading incident archive.") &&
    !archiveText().includes(archive.events[0].title);
  pendingArchive.resolve({ok: true, json: async () => ({schema_version: "2", generated_at: summary.generated_at, events: []})});
  await tick();
  pendingArchive = null;
  summary.generated_at = archive.generated_at;
  history.generated_at = archive.generated_at;
  currentArchive = {schema_version: "2", generated_at: archive.generated_at, events: []};
  refresh();
  await tick();
  old.resolve({ok: true, json: async () => archive});
  await tick();
  const newerWins = archiveText().includes("No resolved incidents or completed maintenance.") &&
    !archiveText().includes(archive.events[0].title);
  currentArchive = {...archive, events: [{...archive.events[0], status: "planned"}]};
  refresh(); await tick();
  const malformed = state() === "ready" && archiveText().includes("Incident archive unavailable.");
  currentArchive = {...archive, generated_at: "2026-09-26T23:39:00Z"};
  refresh(); await tick();
  const incoherent = state() === "ready" && archiveText().includes("Incident archive unavailable.");
  currentArchive = archive;
  windowObject.__STATUS_NOW_MS__ += 180001;
  refresh(); await tick();
  const stale = state() === "ready" && archiveText().includes("Incident archive unavailable.");
  windowObject.__STATUS_NOW_MS__ = Date.parse(archive.generated_at);
  archiveError = true;
  refresh(); await tick();
  const error = state() === "ready" && archiveText().includes("Incident archive unavailable.");
  archiveError = false;
  refresh(); await tick();
  const recovered = state() === "ready" && archiveText().includes(archive.events[0].title);
  process.stdout.write(JSON.stringify({loaded, retained, unchanged, newGenerationPending, newerWins, malformed, incoherent, stale, error, recovered}));
})().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
