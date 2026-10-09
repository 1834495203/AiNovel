<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { computed } from "vue";
import { api, closeStream, type CommandSubmission, type CommandView, type DiscussionSession, type EventData, type RunOverview, type StoryView } from "../api/client";
import { NButton, NCard, NEmpty, NInput, NScrollbar, NTag } from "naive-ui";
import { historyLink, navigate, routeQuery, replaceQuery } from "../ui/navigation";
import { changeText } from "../ui/runPresentation";
import { MessageCircle, Plus, Send } from "@lucide/vue";

const props = defineProps<{ storyId: string }>();
const emit = defineEmits<{ back: []; openStory: [storyId: string] }>();
const story = ref<StoryView | null>(null);
const sessions = ref<DiscussionSession[]>([]);
const sessionId = ref<string | null>(routeQuery().get("session"));
const commands = ref<CommandView[]>([]);
const overviews = ref<Record<string, RunOverview>>({});
const message = ref("");
const status = ref("准备讨论故事走向和资料");
const busy = ref(false);
const monitoring = new Map<string, Promise<CommandView>>();
const sessionCreating = ref(false);
const serverBusy = computed(() => commands.value.some(command => ["queued", "running"].includes(command.status)));

function saveDraft() { localStorage.setItem(`ainovel.discussionDraft.${props.storyId}.${sessionId.value ?? "new"}`, message.value); }
const eventLabels: Record<string, string> = { search_story: "查找相关前文", update_story_settings: "暂存故事资料修改", discussion: "整理讨论回复", planning_decision: "规划本幕", writing: "生成正文", judge: "检查草稿" };
function onEvent(event: EventData) {
  if (event.type === "step.started" && event.trace) status.value = eventLabels[event.trace.node] || event.trace.summary || "正在处理讨论步骤…";
  if (event.type === "connection.retrying") status.value = "连接恢复中，讨论仍在后台运行…";
}
async function refresh() {
  const [view, list] = await Promise.all([
    api.get<StoryView>(`/api/stories/${props.storyId}`),
    api.get<DiscussionSession[]>(`/api/stories/${props.storyId}/discussions`),
  ]);
  story.value = view; sessions.value = list;
  if (!list.some(item => item.id === sessionId.value)) sessionId.value = list[0]?.id ?? null;
  const query = routeQuery();
  if (sessionId.value) query.set("session", sessionId.value); else query.delete("session");
  replaceQuery(query);
  message.value = localStorage.getItem(`ainovel.discussionDraft.${props.storyId}.${sessionId.value ?? "new"}`) ?? "";
  if (sessionId.value) {
    commands.value = await api.get<CommandView[]>(`/api/stories/${props.storyId}/discussions/${sessionId.value}/commands`);
    const overviewPairs = await Promise.all(commands.value.filter(command => command.status !== "queued").map(async command => {
      try { return [command.submission.id, await api.get<RunOverview>(`/api/runs/${command.submission.id}`)] as const; }
      catch { return null; }
    }));
    overviews.value = Object.fromEntries(overviewPairs.filter((pair): pair is readonly [string, RunOverview] => pair !== null));
  } else commands.value = [];
  for (const command of commands.value.filter(item => ["queued", "running"].includes(item.status))) {
    watchCommand(command.submission.id);
  }
}
function watchCommand(id: string) {
  if (monitoring.has(id)) return;
  const pending = api.wait(id, onEvent).finally(() => { monitoring.delete(id); refresh().catch(problem => status.value = problem.message); });
  monitoring.set(id, pending); pending.catch(problem => status.value = problem instanceof Error ? problem.message : "后台任务失败");
}
onMounted(() => refresh().catch(error => status.value = error.message));
onUnmounted(() => { for (const id of monitoring.keys()) closeStream(id); monitoring.clear(); });

async function run(payload: CommandSubmission["payload"], revision = story.value?.revision_id) {
  if (!story.value) throw new Error("无法读取当前作品。");
  return api.command(payload, props.storyId, revision ?? null, null, onEvent);
}
async function createSession() {
  if (sessionCreating.value || busy.value) return;
  sessionCreating.value = true; busy.value = true;
  try { await forceCreateSession(); }
  catch (problem) { status.value = problem instanceof Error ? problem.message : "创建会话失败"; }
  finally { sessionCreating.value = false; busy.value = false; }
}
async function ensureSession() {
  if (sessionId.value) return;
  return forceCreateSession();
}
async function forceCreateSession() {
  const id = crypto.randomUUID();
  const result = await run({ kind: "start_discussion", session_id: id, title: "新讨论" });
  sessionId.value = result.output.discussion_session?.id ?? id;
  await refresh();
}
async function send() {
  const text = message.value.trim(); if (!text || busy.value || serverBusy.value || !story.value) return;
  busy.value = true; status.value = "读取最新故事并开始讨论…";
  const toSend = text; saveDraft();
  try {
    if (!sessionId.value) await ensureSession();
    const latest = await api.get<StoryView>(`/api/stories/${props.storyId}`);
    await api.command({ kind: "discussion", session_id: sessionId.value!, message: toSend }, props.storyId, latest.revision_id, null, onEvent, () => { message.value = ""; saveDraft(); });
    status.value = "本轮讨论已保存"; await refresh();
  } catch (error) { status.value = "讨论未完成，输入仍已保留。"; await refresh().catch(() => undefined); message.value = toSend; saveDraft(); }
  finally { busy.value = false; }
}
</script>

<template>
  <main v-if="story" class="discussion-workspace">
    <div class="discussion-layout">
      <aside class="discussion-sidebar">
        <div class="discussion-sidebar-heading"><div><span class="eyebrow">对话记录</span><h2>讨论会话</h2></div><n-button quaternary circle aria-label="新建讨论" :disabled="busy || serverBusy || sessionCreating" :loading="sessionCreating" @click="createSession"><Plus :size="17"/></n-button></div>
        <p>可以推敲剧情、人物和世界规则。需要写正文时回到创作区。</p>
        <n-scrollbar class="session-scroll"><button v-for="session in sessions" :key="session.id" class="session-row" :class="{ selected: session.id === sessionId }" :disabled="busy" @click="sessionId = session.id; refresh()"><MessageCircle :size="15"/><span><strong>{{ session.title }}</strong><small>{{ session.message_count }} 轮</small></span></button></n-scrollbar>
        <n-button block secondary :disabled="busy || serverBusy || sessionCreating" @click="createSession"><template #icon><Plus :size="16"/></template>新讨论</n-button>
      </aside>
      <section class="discussion-chat-area">
        <div class="discussion-chat-heading"><div><span class="eyebrow">当前会话</span><h2>{{ sessions.find(session => session.id === sessionId)?.title || '新讨论' }}</h2></div><n-tag v-if="serverBusy || busy" type="info" round>处理中</n-tag><span v-else class="muted">{{ status }}</span></div>
        <n-scrollbar class="discussion-scroll" ref="discussionScroll">
          <div class="discussion-messages" aria-live="polite">
            <article v-for="command in commands" :key="command.submission.id" class="discussion-turn">
              <div v-if="command.submission.payload.kind === 'discussion'" class="chat-bubble user-bubble"><small>你</small><p>{{ command.submission.payload.message }}</p></div>
              <div v-else class="session-event"><MessageCircle :size="14"/> 已创建讨论会话</div>
              <n-card v-if="command.output.discussion" class="assistant-card" size="small" :bordered="false">
                <div class="assistant-label"><span class="assistant-mark">AI</span><strong>故事讨论</strong></div>
                <p class="assistant-reply">{{ command.output.discussion.reply }}</p>
                <n-tag size="small" round :type="command.output.discussion.settings_changed ? 'success' : 'default'">{{ command.output.discussion.settings_changed ? '资料已更新 · 正文未改动' : '资料未修改 · 正文未改动' }}</n-tag>
                <div v-if="overviews[command.submission.id]?.change_set.entries.length" class="discussion-changes"><p v-for="(change,index) in overviews[command.submission.id].change_set.entries" :key="index">{{ change.category }} · {{ change.path }}：{{ changeText(change.after) || '已移除' }}</p></div>
              </n-card>
              <n-card v-else-if="command.status === 'failed'" class="failed-turn" size="small"><template #header>本轮讨论失败</template><p>{{ command.error || '故事资料没有写入。' }}</p><n-button text @click="navigate(historyLink(storyId, command.submission.id))">在运行历史中处理</n-button></n-card>
              <n-card v-else class="pending-turn" size="small"><n-tag type="info" size="small">{{ status }}</n-tag></n-card>
            </article>
            <div v-if="!commands.length" class="discussion-empty"><n-empty description="下一步，故事可以怎样发展？"><template #extra><p>聊剧情、推敲人物与世界规则。明确要求修改时，助手会更新资料并列出变化。</p></template></n-empty><div class="suggestion-list"><n-button v-for="idea in ['梳理主线与尚未回收的伏笔。','讨论下一幕的几个可能走向。','看看人物的发展是否需要调整。']" :key="idea" secondary size="small" @click="message = idea">{{ idea }}</n-button></div></div>
          </div>
        </n-scrollbar>
    <footer class="studio-composer discussion-composer"><div class="studio-composer-inner"><form @submit.prevent="send"><n-input v-model:value="message" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }" :disabled="busy || serverBusy" placeholder="聊聊后续剧情，或明确告诉 AI 要修改哪些资料…" @input="saveDraft" @keydown.enter.exact.prevent="send"/><div class="composer-actions"><span class="muted" role="status">{{ status }} · 输入会自动保存；Shift + 回车换行</span><n-button type="primary" attr-type="submit" :loading="busy || serverBusy" :disabled="!message.trim()"><template #icon><Send :size="15"/></template>发送</n-button></div></form></div></footer>
      </section>
    </div>

  </main>
  <main v-else class="loading-state">正在读取故事讨论…</main>
</template>
