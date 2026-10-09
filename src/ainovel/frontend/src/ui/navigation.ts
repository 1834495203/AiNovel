export function routeQuery(): URLSearchParams {
  return new URLSearchParams(location.hash.includes("?") ? location.hash.split("?")[1] : location.search);
}

export function navigate(url: string) {
  history.pushState(null, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function historyLink(scope?: string, command?: string): string {
  const query = new URLSearchParams();
  if (scope) query.set("scope", scope);
  if (command) query.set("command", command);
  return `/#history${query.size ? "?" + query : ""}`;
}

export function replaceQuery(query: URLSearchParams) {
  const base = location.hash.split("?")[0];
  history.replaceState(null, "", base ? `/${base}?${query}` : `${location.pathname}?${query}`);
}
