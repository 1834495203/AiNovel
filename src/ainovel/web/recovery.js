import { button, element } from "./client.js";

/** Failed operations remain visible after reload and offer explicit command recovery. */
export function failureBubble(command, recover) {
  const node = element("div", "", "bubble failure");
  node.dataset.failedCommand = command.submission.id;
  const payload = command.submission.payload;
  const request = payload.instruction || payload.feedback || payload.message || payload.turns?.at(-1)?.content;
  if (request) node.append(element("small","请求：" + request));
  node.append(element("small","任务未完成"),element("div",command.error || "执行失败"));
  node.append(button("重试",() => recover(command,"retry")),button("撤回这次失败",() => recover(command,"withdraw")));
  return node;
}
