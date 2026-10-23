"use strict";
/**
 * @typedef {Event & { waitUntil(promise: Promise<unknown>): void }} ExtendableEvent
 * @typedef {ExtendableEvent & { request: Request, respondWith(response: Promise<Response | undefined> | Response): void }} FetchEvent
 * @typedef {{ skipWaiting(): Promise<void>, clients: { claim(): Promise<void> }, addEventListener(type: string, listener: (event: Event) => void): void }} ServiceWorkerGlobalScope
 */
const worker = /** @type {ServiceWorkerGlobalScope} */ (/** @type {unknown} */ (self));
const scope = "__PROJECT_SCOPE__";
const cacheName = "devin-remote-v3";
const shell = [scope, `${scope}manifest.webmanifest`, "/assets/app.css", "/assets/app.js"];
worker.addEventListener("install", event => {
  /** @type {ExtendableEvent} */ (event)
    .waitUntil(caches.open(cacheName).then(cache => cache.addAll(shell)));
  worker.skipWaiting();
});
worker.addEventListener("activate", event => {
  /** @type {ExtendableEvent} */ (event).waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== cacheName).map(key => caches.delete(key))
    ))
  );
  worker.clients.claim();
});
worker.addEventListener("fetch", event => {
  const fetchEvent = /** @type {FetchEvent} */ (event);
  if (fetchEvent.request.method !== "GET") return;
  const url = new URL(fetchEvent.request.url);
  if (url.pathname.includes("/api/")) return;
  fetchEvent.respondWith(
    fetch(fetchEvent.request)
      .then(response => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(cacheName).then(cache => cache.put(fetchEvent.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(fetchEvent.request).then(match => match ?? caches.match(scope)))
  );
});
