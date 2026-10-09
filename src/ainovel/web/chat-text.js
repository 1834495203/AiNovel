import { element } from "./client.js";

/** A small safe Markdown view for chat text; model HTML is always treated as text. */
function inline(parent, text) {
  const pattern=/\*\*([^*]+)\*\*|`([^`]+)`/g; let offset=0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(offset,match.index)),element(match[1] ? "strong" : "code",match[1] || match[2]));
    offset=match.index+match[0].length;
  }
  parent.append(document.createTextNode(text.slice(offset)));
}
export function chatText(text) {
  const body=element("div","","chat-text"); let list=null, code=null;
  for (const line of text.split("\n")) {
    if (line.startsWith("```")) { if (code) code=null; else { code=element("pre");body.append(code); } list=null;continue; }
    if (code) { code.textContent+=line+"\n";continue; }
    const heading=line.match(/^(#{1,3})\s+(.+)/), item=line.match(/^\s*(?:([-*])|\d+\.)\s+(.+)/);
    if (item) { const kind=item[1] ? "ul" : "ol";if (!list || list.tagName.toLowerCase() !== kind) { list=element(kind);body.append(list); } const row=element("li");inline(row,item[2]);list.append(row);continue; }
    list=null;
    if (!line.trim()) continue;
    const row=element(heading ? "h"+(heading[1].length+1) : "p");inline(row,heading ? heading[2] : line);body.append(row);
  }
  return body;
}
