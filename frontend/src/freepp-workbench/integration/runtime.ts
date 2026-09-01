import { useSyncExternalStore } from "react";

export type RuntimeAccount = {
  sessionId: string;
  email: string;
  accountId: string;
  accessToken: string;
  sessionToken: string;
  tokenId: string;
  country: string;
  status: string;
};

export type RuntimeProxy = {
  id: string;
  address: string;
  country: string;
  purposeTags: string[];
  status: string;
  enabled: boolean;
  latencyMs?: number;
};

export type RuntimeSnapshot = {
  selectedAccountIds: string[];
  accounts: RuntimeAccount[];
  proxies: RuntimeProxy[];
  loadedAt: number;
  lastError: string;
};

const empty: RuntimeSnapshot = { selectedAccountIds: [], accounts: [], proxies: [], loadedAt: 0, lastError: "" };
let snapshot = empty;
const listeners = new Set<() => void>();

function publish(next: RuntimeSnapshot) {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

export function getRuntimeSnapshot() { return snapshot; }
export function subscribeRuntime(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function useRuntime() {
  return useSyncExternalStore(subscribeRuntime, getRuntimeSnapshot, getRuntimeSnapshot);
}
export function setRuntimeSelection(ids: string[], accounts: RuntimeAccount[] = snapshot.accounts) {
  const selected = new Set(ids.map(String));
  publish({ ...snapshot, selectedAccountIds: Array.from(selected), accounts: accounts.filter((account) => selected.has(String(account.sessionId))) });
}
export function upsertRuntimeAccount(account: RuntimeAccount) {
  const accounts = [...snapshot.accounts.filter((item) => item.sessionId !== account.sessionId), account];
  publish({ ...snapshot, accounts, selectedAccountIds: snapshot.selectedAccountIds.includes(account.sessionId) ? snapshot.selectedAccountIds : [...snapshot.selectedAccountIds, account.sessionId] });
}
export function updateRuntimeAccount(sessionId: string, patch: Partial<RuntimeAccount>) {
  const current = snapshot.accounts.find((account) => account.sessionId === sessionId);
  if (current) upsertRuntimeAccount({ ...current, ...patch, sessionId });
}
export function setRuntimeProxies(proxies: RuntimeProxy[], lastError = "") { publish({ ...snapshot, proxies, loadedAt: Date.now(), lastError }); }
export function setRuntimeError(lastError: string) { publish({ ...snapshot, lastError }); }
export function runtimeContext() {
  return {
    accounts: snapshot.accounts.map(({ sessionId, email, accountId, accessToken, sessionToken, tokenId, country }) => ({ session_id: sessionId, email, account_id: accountId, access_token: accessToken, session_token: sessionToken, token_id: tokenId, country })),
    proxies: snapshot.proxies.map(({ id, address, country, purposeTags, status, enabled }) => ({ id, address, country, purpose_tags: purposeTags, status, enabled })),
  };
}
export function proxyPoolForRequest() {
  return snapshot.proxies.map(({ id, address, country, purposeTags, status, enabled }) => ({ id, address, country, purpose_tags: purposeTags, status, enabled }));
}
export function pickRuntimeProxy(country: string, purpose = "") {
  const cc = String(country || "").toUpperCase();
  const required = String(purpose || "").toLowerCase();
  return snapshot.proxies.find((proxy) => proxy.enabled && (!cc || proxy.country === cc) && (!required || !proxy.purposeTags.length || proxy.purposeTags.includes(required)))?.address || "";
}
