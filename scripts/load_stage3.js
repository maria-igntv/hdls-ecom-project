import http from 'k6/http';
import encoding from 'k6/encoding';
import crypto from 'k6/crypto';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';

const model = JSON.parse(open('/scripts/capacity.json'));
const names = { OP01: 'category', OP02: 'card', OP04: 'cartRead', OP05: 'cartWrite',
  OP06: 'checkout', OP09: 'history', OP10: 'detail' };
const weights = {};
for (const operation of model.operations.filter(operation => names[operation.id])) {
  weights[names[operation.id]] = model.audience.dau * operation.actions * (1 + operation.retry);
}
weights.catalogUpdate = model.commerce.catalog_updates_day;
weights.stockUpdate = model.commerce.stock_updates_day;
const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
const rate = Number(__ENV.RATE || 521);
const duration = Number(__ENV.SECONDS || 120);
const run = __ENV.RUN || 'manual';
const base = __ENV.BASE_URL || 'http://localhost:18080';
const latency = new Trend('operation_latency', true);
const technicalErrors = new Rate('technical_errors');
const operationErrors = new Rate('operation_errors');
const operations = new Counter('operation_requests');
const unexpected = new Counter('unexpected_responses');
const supportErrors = new Rate('support_errors');
const latencies = Object.fromEntries(Object.keys(weights).map(name => [name, new Trend(`latency_${name}`, true)]));
const minutes = Array.from({ length: Math.ceil(duration / 60) }, (_, minute) => ({
  latency: new Trend(`minute_latency_${minute}`, true),
  errors: new Rate(`minute_technical_errors_${minute}`),
  operations: new Counter(`minute_operations_${minute}`),
}));
const scenarios = {};
// Allocate whole arrivals per minute. The rounding error is reported by the runner.
for (const [name, weight] of Object.entries(weights)) {
  const perMinute = Math.max(1, Math.round(rate * 60 * weight / total));
  scenarios[name] = {
    executor: 'constant-arrival-rate', exec: name, rate: perMinute, timeUnit: '1m',
    duration: `${duration}s`, gracefulStop: '30s',
    preAllocatedVUs: Math.max(8, Math.ceil(perMinute / 60)),
    maxVUs: Math.max(10, Math.ceil(perMinute / 60 * 2)),
  };
}

export const options = {
  scenarios,
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'p(50)', 'p(95)', 'p(99)', 'max'],
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
  thresholds: {
    technical_errors: ['rate<0.001'], operation_errors: ['rate<0.001'],
    support_errors: ['rate<0.001'], dropped_iterations: ['count==0'],
    operation_latency: ['p(95)<200', 'p(99)<500'],
    latency_checkout: ['p(95)<500', 'p(99)<1000'],
    ...Object.fromEntries(Object.keys(weights).filter(name => name !== 'checkout')
      .map(name => [`latency_${name}`, ['p(95)<200', 'p(99)<500']])),
    ...Object.fromEntries(Object.keys(weights).map(name => [`dropped_iterations{scenario:${name}}`, ['count==0']])),
    ...Object.fromEntries(minutes.flatMap((_, minute) => [
      [`minute_latency_${minute}`, ['p(95)<200', 'p(99)<500']],
      [`minute_technical_errors_${minute}`, ['rate<0.001']],
    ])),
  },
};

function params(name, user, body = false) {
  const headers = {};
  if (user) {
    const credentials = user === 'admin' ? 'admin:demo-admin' : `buyer-${user}:demo-buyer-${user}`;
    headers.Authorization = `Basic ${encoding.b64encode(credentials)}`;
  }
  if (body) headers['Content-Type'] = 'application/json';
  return { headers, tags: { name, role: 'primary' }, timeout: '20s' };
}

function record(name, response, expected = 200) {
  const minute = Math.min(minutes.length - 1, Math.floor((Date.now() - exec.scenario.startTime) / 60000));
  minutes[minute].latency.add(response.timings.duration);
  minutes[minute].errors.add(response.status === 0 || response.status >= 500);
  minutes[minute].operations.add(1);
  operations.add(1, { operation: name });
  latency.add(response.timings.duration);
  latencies[name].add(response.timings.duration);
  technicalErrors.add(response.status === 0 || response.status >= 500);
  operationErrors.add(response.status !== expected);
  if (response.status !== expected) {
    unexpected.add(1, { operation: name, status: String(response.status) });
  }
}

export function category() {
  const category = 1 + Math.floor(Math.random() * 20);
  const cursor = Math.floor(Math.random() * 4900) * 20;
  record('category', http.get(`${base}/api/products?categoryId=${category}&afterId=${cursor}&limit=20`, params('category')));
}

export function card() {
  const id = 1 + Math.floor(Math.random() * (Math.random() < 0.95 ? 1000 : 100000));
  record('card', http.get(`${base}/api/products/${id}`, params('card')));
}

function cartUser() { return 201 + (exec.vu.idInTest % 600); }
export function cartRead() { record('cartRead', http.get(`${base}/api/cart`, params('cartRead', cartUser()))); }
export function cartWrite() {
  const user = cartUser();
  record('cartWrite', http.put(`${base}/api/cart/items/${10000 + user}`, JSON.stringify({ quantity: 1 }), params('cartWrite', user, true)));
}

export function checkout() {
  const iteration = exec.scenario.iterationInTest;
  const user = 1 + (iteration % 200);
  let cart;
  // Separate checkout users and SKUs keep this capacity test free of flash-sale conflicts.
  for (let item = 0; item < 3; item++) {
    const request = params('checkout_setup', user, true);
    request.tags.role = 'support';
    request.responseType = 'text';
    const response = http.put(`${base}/api/cart/items/${60000 + user * 3 + item}`, JSON.stringify({ quantity: 1 }), request);
    supportErrors.add(response.status !== 200);
    if (response.status !== 200) return;
    cart = response.json();
  }
  const request = params('checkout', user, true);
  request.headers['Idempotency-Key'] = `${run}-${iteration}`;
  record('checkout', http.post(`${base}/api/orders`, JSON.stringify({
    expectedCartVersion: cart.version, expectedTotalMinor: cart.totalMinor,
  }), request), 201);
}

function historyUser() { return 801 + (exec.vu.idInTest % 200); }
export function history() { record('history', http.get(`${base}/api/orders`, params('history', historyUser()))); }
export function detail() {
  const user = historyUser();
  const hash = crypto.md5(`history-${user - 1}`, 'hex');
  const id = `${hash.slice(0, 8)}-${hash.slice(8, 12)}-${hash.slice(12, 16)}-${hash.slice(16, 20)}-${hash.slice(20)}`;
  record('detail', http.get(`${base}/api/orders/${id}`, params('detail', user)));
}

export function catalogUpdate() {
  const id = 90001 + (exec.scenario.iterationInTest % 1000);
  record('catalogUpdate', http.put(`${base}/api/admin/products/${id}`, JSON.stringify({
    name: `Product ${id}`, priceMinor: 10000 + id * 100, active: true,
  }), params('catalogUpdate', 'admin', true)));
}

export function stockUpdate() {
  const id = 95001 + (exec.scenario.iterationInTest % 1000);
  record('stockUpdate', http.put(`${base}/api/admin/inventory/${id}`, JSON.stringify({ onHand: 1000 }), params('stockUpdate', 'admin', true)));
}

export function handleSummary(data) {
  const configuredRate = Object.values(scenarios).reduce((sum, scenario) => sum + scenario.rate / 60, 0);
  data.profile = { run, requestedRate: rate, configuredRate, duration, weights };
  return { [`/results/${run}-k6.json`]: JSON.stringify(data, null, 2) };
}
