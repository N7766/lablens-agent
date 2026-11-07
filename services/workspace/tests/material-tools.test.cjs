'use strict';
// Actual material-tools.js in a dependency-free DOM harness. No network/provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../src/main/resources/static/material-tools.js'), 'utf8');
const ids = ['workspace', 'bulk-status', 'bulk-cancel', 'bulk-submit', 'bulk-refresh',
  'bulk-files', 'refresh', 'index-status', 'index-refresh', 'index-build'];
const pendingKey = w => `lablens.import.pending.${w}`;
const lastKey = w => `lablens.import.last.${w}`;
const plain = value => JSON.parse(JSON.stringify(value));
const gate = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
};
const ok = body => ({ok: true, status: 200, json: async () => plain(body)});
const rejected = (status, message) => ({ok: false, status, json: async () => ({message})});
const batch = (id = 'batch-1', status = 'QUEUED') => ({id, status, document_count: 1,
  applied_revision: status === 'APPLIED' ? 2 : null, error_message: null});
const file = (name = 'note.txt', text = 'Evidence text') => ({name, size: Buffer.byteLength(text), text: async () => text});

function harness({session = {}, local = {}, handler} = {}) {
  const nodes = Object.fromEntries(ids.map(id => [id, {id, value: '', textContent: '', hidden: false,
    files: [], listeners: {}, addEventListener(event, fn) {this.listeners[event] = fn;},
    click() {return this.onclick?.();}}]));
  const requests = [];
  let uuids = 0, refreshes = 0;
  nodes.workspace.value = 'workspace-a';
  nodes['bulk-files'].files = [file()];
  nodes.refresh.onclick = () => {refreshes++;};
  const storage = target => ({getItem: key => target[key] ?? null,
    setItem: (key, value) => {target[key] = String(value);}, removeItem: key => {delete target[key];}});
  const context = vm.createContext({document: {getElementById: id => nodes[id]},
    sessionStorage: storage(session), localStorage: storage(local),
    crypto: {randomUUID: () => `uuid-${++uuids}`},
    fetch: async (url, options = {}) => {
      const request = {url, method: options.method || 'GET', headers: plain(options.headers || {}),
        body: options.body === undefined ? undefined : JSON.parse(options.body)};
      requests.push(request);
      if (handler) return handler(request, requests.length);
      if (url.endsWith('/documents')) return ok({revision: 1, documents: []});
      if (request.method === 'POST' && url.endsWith('/imports')) return ok(batch());
      if (url.includes('/imports/')) return ok(batch());
      if (url.includes('/index')) return ok({status: 'ready', workspace_index_status: 'ready', revision: 1, chunk_count: 1});
      throw new Error(`Unexpected request: ${request.method} ${url}`);
    }});
  vm.runInContext(source, context);
  return {nodes, requests, session, local,
    click: id => nodes[id].onclick(),
    switchWorkspace: workspace => {nodes.workspace.value = workspace; nodes.workspace.listeners.change();},
    get refreshes() {return refreshes;}};
}

test('unknown import submission retains original body and idempotency key for an explicit retry', async () => {
  let postCount = 0;
  const h = harness({handler: request => {
    if (request.url.endsWith('/documents')) return ok({revision: 7, documents: []});
    if (++postCount === 1) throw new Error('Connection lost after sending');
    return ok(batch());
  }});
  await h.click('bulk-submit');
  const original = JSON.parse(h.session[pendingKey('workspace-a')]);
  assert.match(h.nodes['bulk-status'].textContent, /同一幂等键/);
  h.nodes['bulk-files'].files = [file('different.txt', 'Different request must not replace pending data')];
  await h.click('bulk-submit');
  const posts = h.requests.filter(r => r.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].headers['Idempotency-Key'], original.key);
  assert.equal(posts[1].headers['Idempotency-Key'], original.key);
  assert.deepEqual(posts[1].body, posts[0].body);
  assert.equal(posts[1].body.documents[0].title, 'note.txt');
  assert.equal(h.session[pendingKey('workspace-a')], undefined);
  assert.equal(h.local[lastKey('workspace-a')], 'batch-1');
});

test('pending unknown import survives page reload and does not need files to confirm original request', async () => {
  const pending = {key: 'retained-key', body: {expected_revision: 3,
    documents: [{id: 'd', title: 'retained', text: 'Original', source: 'original.txt'}]}};
  const h = harness({session: {[pendingKey('workspace-a')]: JSON.stringify(pending)}});
  h.nodes['bulk-files'].files = [];
  await h.click('bulk-submit');
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].method, 'POST');
  assert.equal(h.requests[0].headers['Idempotency-Key'], pending.key);
  assert.deepEqual(h.requests[0].body, pending.body);
});

test('batch and index refresh are GET-only and never build an index or retry an import', async () => {
  const h = harness({local: {[lastKey('workspace-a')]: 'batch-previous'}});
  await h.click('bulk-refresh');
  await h.click('index-refresh');
  assert.equal(h.requests.length, 2);
  assert.ok(h.requests.every(r => r.method === 'GET'));
  assert.equal(h.requests[0].url, '/api/workspaces/workspace-a/imports/batch-previous');
  assert.equal(h.requests[1].url, '/api/workspaces/workspace-a/index?strategy=bm25');
  assert.equal(h.refreshes, 1);
});

test('revision conflict discards rejected intent and a later explicit submit rereads revision', async () => {
  let revision = 5, rejectNext = true;
  const h = harness({handler: request => {
    if (request.url.endsWith('/documents')) return ok({revision, documents: []});
    if (rejectNext) {rejectNext = false; revision = 6; return rejected(409, 'Revision changed');}
    return ok(batch('batch-new'));
  }});
  await h.click('bulk-submit');
  assert.equal(h.session[pendingKey('workspace-a')], undefined);
  assert.match(h.nodes['bulk-status'].textContent, /未受理/);
  await h.click('bulk-submit');
  const posts = h.requests.filter(r => r.method === 'POST');
  assert.deepEqual(posts.map(r => r.body.expected_revision), [5, 6]);
  assert.notEqual(posts[0].headers['Idempotency-Key'], posts[1].headers['Idempotency-Key']);
});

test('old workspace import completion keeps its receipt without overwriting another workspace UI', async () => {
  const accepted = gate(), started = gate();
  const h = harness({handler: async request => {
    if (request.url.endsWith('/documents')) return ok({revision: 1, documents: []});
    started.resolve(); await accepted.promise; return ok(batch('batch-a', 'APPLIED'));
  }});
  const task = h.click('bulk-submit');
  await started.promise;
  h.switchWorkspace('workspace-b');
  const status = h.nodes['bulk-status'].textContent;
  accepted.resolve(); await task;
  assert.equal(h.nodes['bulk-status'].textContent, status);
  assert.equal(h.local[lastKey('workspace-a')], 'batch-a');
  assert.equal(h.local[lastKey('workspace-b')], undefined);
});

test('A to B to A navigation invalidates an earlier index response', async () => {
  const delayed = gate(), started = gate();
  const h = harness({handler: async () => {started.resolve(); await delayed.promise;
    return ok({status: 'ready', revision: 1, chunk_count: 999});}});
  const task = h.click('index-refresh');
  await started.promise;
  h.switchWorkspace('workspace-b'); h.switchWorkspace('workspace-a');
  const status = h.nodes['index-status'].textContent;
  delayed.resolve(); await task;
  assert.equal(h.nodes['index-status'].textContent, status);
  assert.doesNotMatch(h.nodes['index-status'].textContent, /999/);
});

test('changing workspace while document revision is loading prevents a new import POST', async () => {
  const delayed = gate(), started = gate();
  const h = harness({handler: async request => {
    if (request.url.endsWith('/documents')) {started.resolve(); await delayed.promise;
      return ok({revision: 1, documents: []});}
    return ok(batch());
  }});
  const task = h.click('bulk-submit');
  await started.promise;
  h.switchWorkspace('workspace-b'); h.switchWorkspace('workspace-a');
  delayed.resolve(); await task;
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 0);
});

test('pending unknown submission is visible even when a previous batch receipt exists', async () => {
  const pending = {key: 'unknown-new-key', body: {expected_revision: 7, documents: []}};
  const h = harness({session: {[pendingKey('workspace-a')]: JSON.stringify(pending)},
    local: {[lastKey('workspace-a')]: 'old-completed-batch'},
    handler: () => ok(batch('old-completed-batch', 'APPLIED'))});
  await h.click('bulk-refresh');
  assert.match(h.nodes['bulk-status'].textContent, /不明|未知|确认/);
  assert.doesNotMatch(h.nodes['bulk-status'].textContent, /整批已保存/);
  assert.equal(h.requests.filter(r => r.method === 'POST').length, 0);
  assert.ok(h.session[pendingKey('workspace-a')]);
});

test('the material index button only requests local BM25 with its observed revision', async () => {
  const h = harness();
  await h.click('index-build');
  const posts = h.requests.filter(r => r.method === 'POST');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, '/api/workspaces/workspace-a/index');
  assert.deepEqual(posts[0].body, {strategy: 'bm25', expected_revision: 1});
  assert.equal(posts[0].body.approved_revision, undefined);
});
