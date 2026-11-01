<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { darkTheme, dateZhCN, zhCN, NButton, NCard, NConfigProvider, NDialogProvider, NEmpty, NMessageProvider, NTag, type GlobalThemeOverrides } from "naive-ui";
import { BookOpen, Moon, Plus, Sun, PanelLeft, MessageCircle, Users, FileText, Settings, Trash2, Search } from "@lucide/vue";
import { api, type SetupDraft, type StorySummary } from "./api/client";
import CreationPage from "./components/CreationPage.vue";
import Workbench from "./components/Workbench.vue";
import DiscussionPage from "./components/DiscussionPage.vue";
import CharacterPages from "./components/CharacterPages.vue";

import MaterialsPage from "./components/MaterialsPage.vue";
import FieldTemplatesPage from "./components/FieldTemplatesPage.vue";
import { routeQuery } from "./ui/navigation";

type Route = { kind: "shelf" | "new" | "draft" | "story" | "discussion" | "characters" | "character" | "materials" | "field-templates"; id: string };
function parseRoute(): Route {
  const hash = location.hash.split('?')[0];
  const match = hash.match(/^#(story|draft|discussion|materials)\/([0-9a-f-]{36})$/i);
  if (match) return { kind: match[1].toLowerCase() as Route['kind'], id: match[2] };
  if (hash === '#history' || location.pathname === '/history') return { kind:'shelf', id:'' };
  const kind = hash.replace(/^#/, '') || location.pathname.replace(/^\//, '') || 'shelf';
  if (['new','characters','character','field-templates'].includes(kind)) return { kind: kind as Route['kind'], id: routeQuery().get('story') || routeQuery().get('scope') || '' };
  if (kind === 'discussion') return {kind:'discussion',id:routeQuery().get('story') || ''};
  return { kind:'shelf',id:'' };
}
const route = ref<Route>(parseRoute());
const stories = ref<StorySummary[]>([]);
const drafts = ref<SetupDraft[]>([]);
const trash = ref<StorySummary[]>([]);
const trashedDrafts = ref<SetupDraft[]>([]);
const trashOpen = ref(false);
const shelfBusy = ref(false);
const health = ref("连接中…");
const error = ref("");
const colorMode = ref(localStorage.getItem("ainovel.theme") || "light");
const routeKey = computed(() => `${route.value.kind}:${location.hash}:${location.search}`);
const railOpen = ref(false);
const activeStoryId = computed(() => {
  if (["story", "discussion", "characters", "character", "materials"].includes(route.value.kind)) return route.value.id || routeQuery().get("story") || "";
  return "";
});
const themeOverrides: GlobalThemeOverrides = {
  common: { primaryColor: "#536bce", primaryColorHover: "#435bc0", primaryColorPressed: "#374fae", primaryColorSuppl: "#536bce", borderRadius: "8px", fontFamily: "Inter, 'Segoe UI', 'Microsoft YaHei', system-ui, sans-serif" },
};

function syncRoute() { route.value = parseRoute(); railOpen.value = false; }
function navigate(target: string) { history.pushState(null, "", target); syncRoute(); }
function openStory(id: string) { navigate(`/#story/${id}`); }
function openDraft(id: string) { navigate(`/#draft/${id}`); }
function showShelf() { navigate("/"); loadShelf().catch(problem => error.value = problem.message); }
function openDiscussion(id: string) { navigate(`/#discussion/${id}`); }
function openCharacters(id: string) { navigate(`/#characters?story=${id}`); }
async function loadShelf() {
  const [books, draftsList, status] = await Promise.all([
    api.get<StorySummary[]>("/api/stories"), api.get<SetupDraft[]>("/api/drafts"), api.get<{ database: boolean; redis: boolean; worker: boolean; provider: string; model: string }>("/health"),
  ]);
  stories.value = books; drafts.value = draftsList;
  if (trashOpen.value) await loadTrash();
  health.value = status.database && status.redis && status.worker ? `${status.provider === "mock" ? "演示模式" : status.model} · 已连接` : "后台服务需要检查";
}
onMounted(() => {
  document.documentElement.dataset.theme = colorMode.value;
  window.addEventListener("hashchange", syncRoute); window.addEventListener("popstate", syncRoute);
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element)?.closest<HTMLAnchorElement>('a[href]');
    if (!anchor || anchor.target || anchor.origin !== location.origin) return;
    if (['/character','/characters','/discussion'].includes(anchor.pathname)) { event.preventDefault(); navigate(`/#${anchor.pathname.slice(1)}${anchor.search}`); }
    else if (anchor.pathname === '/' && anchor.hash) { event.preventDefault(); if (/^#(?:character|chapter|scene)-/.test(anchor.hash)) document.getElementById(anchor.hash.slice(1))?.scrollIntoView({ block: 'start' }); else navigate('/' + anchor.hash); }
  });
  loadShelf().catch(problem => error.value = problem.message);
});
function toggleTheme() { colorMode.value = colorMode.value === "light" ? "dark" : "light"; localStorage.setItem("ainovel.theme", colorMode.value); document.documentElement.dataset.theme = colorMode.value; }
async function restoreStory(id: string) {
  if (shelfBusy.value) return;
  shelfBusy.value = true;
  try {
    const deleted = trash.value.find(book => book.id === id);
    if (!deleted) throw new Error("回收站记录已刷新，请重试。");
    await api.command({ kind: "restore_story" }, id, deleted.revision_id, null, () => undefined);
    await loadShelf(); error.value = "作品已恢复。";
  } catch (problem) { error.value = problem instanceof Error ? problem.message : "恢复失败"; }
  finally { shelfBusy.value = false; }
}
async function loadTrash() {
  [trash.value, trashedDrafts.value] = await Promise.all([
    api.get<StorySummary[]>("/api/stories?deleted=true"), api.get<SetupDraft[]>("/api/drafts?deleted=true"),
  ]);
}
function toggleTrash() {
  trashOpen.value = !trashOpen.value;
  if (trashOpen.value) loadTrash().catch(problem => error.value = problem.message);
}
async function setDraftDeleted(id: string, deleted: boolean) {
  if (shelfBusy.value || (deleted && !window.confirm("将创作草稿移入回收站？构思与访谈记录可之后恢复。"))) return;
  shelfBusy.value = true;
  try {
    await api.command({ kind: "set_setup_draft_deleted", deleted }, null, null, id, () => undefined);
    await loadShelf(); error.value = deleted ? "创作草稿已移入回收站。" : "创作草稿已恢复。";
  } catch (problem) { error.value = problem instanceof Error ? problem.message : "操作失败"; }
  finally { shelfBusy.value = false; }
}
async function permanentlyDelete(id: string, title: string, draft: boolean) {
  if (shelfBusy.value || !window.confirm(`永久删除「${title}」？正文、角色、构思访谈、全部版本、运行历史及相关资料将彻底删除，无法恢复。`)) return;
  shelfBusy.value = true;
  try {
    const result = await api.remove(`/api/${draft ? 'drafts' : 'stories'}/${id}`);
    // Drop cached input/regeneration state belonging to this resource as well.
    const cacheIds = [id, ...(result.cache_ids ?? [])];
    for (const key of Object.keys(localStorage)) if (key.startsWith('ainovel.') && cacheIds.some(value => key.includes(value))) localStorage.removeItem(key);
    await loadShelf(); error.value = "已永久删除相关内容及运行历史，无法恢复。";
  } catch (problem) { error.value = problem instanceof Error ? problem.message : "永久删除失败"; }
  finally { shelfBusy.value = false; }
}
async function workAction(action: string) {
  if (!activeStoryId.value) return;
  if (route.value.kind !== 'story') { openStory(activeStoryId.value); await nextTick(); }
  window.dispatchEvent(new CustomEvent('ainovel:work-action', { detail: action }));
  railOpen.value = false;
}
let railPointer = 0;
function railPointerDown(event: PointerEvent) { railPointer = event.clientX; }
function railPointerUp(event: PointerEvent) { if (event.clientX - railPointer > 25) railOpen.value = true; else if (railPointer - event.clientX > 25) railOpen.value = false; }
</script>

<template>
  <n-config-provider :theme="colorMode === 'dark' ? darkTheme : null" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
  <n-dialog-provider><n-message-provider>
  <div class="app-shell single-app" :data-theme="colorMode">
    <header class="topbar" :class="{ 'writing-header': route.kind === 'story' }">
      <a class="brand" href="/" @click.prevent="showShelf"><BookOpen :size="18" stroke-width="1.8" /><span>纸间</span></a>
      <div class="topbar-context"><span v-if="route.kind === 'shelf'" class="health-pill">{{ health }}</span><span v-else class="muted">{{ route.kind === 'field-templates' ? '字段模板' : stories.find(b => b.id === activeStoryId)?.title || '写作空间' }}</span></div>
      <div id="workspace-header-controls"></div>
      <div class="topbar-actions">
        <n-button quaternary circle :aria-label="colorMode === 'light' ? '切换深色模式' : '切换浅色模式'" @click="toggleTheme"><Moon v-if="colorMode === 'light'" :size="17"/><Sun v-else :size="17"/></n-button>
        <n-button v-if="route.kind === 'shelf'" type="primary" @click="navigate('/#new')"><template #icon><Plus :size="17"/></template>新作品</n-button>
      </div>
    </header>
    <aside class="project-rail" :class="{ expanded: railOpen }" @pointerdown="railPointerDown" @pointerup="railPointerUp">
      <button class="rail-button rail-expander" aria-label="展开或收起作品侧栏" @click="railOpen = !railOpen"><PanelLeft :size="20"/><span>作品导航</span></button>
      <button class="rail-button" aria-label="书架" :class="{ active: route.kind === 'shelf' }" :aria-current="(route.kind === 'shelf') ? 'page' : undefined" @click="showShelf"><BookOpen :size="19"/><span>书架</span></button>
      <button class="rail-button" aria-label="字段模板" :class="{ active: route.kind === 'field-templates' }" :aria-current="route.kind === 'field-templates' ? 'page' : undefined" @click="navigate('/#field-templates')"><Settings :size="19"/><span>字段模板</span></button>
      <template v-if="activeStoryId">
        <button class="rail-button" aria-label="小说工作区" :class="{ active: route.kind === 'story' }" :aria-current="(route.kind === 'story') ? 'page' : undefined" @click="openStory(activeStoryId)"><FileText :size="19"/><span>小说工作区</span></button>
        <button class="rail-button" aria-label="故事讨论" :class="{ active: route.kind === 'discussion' }" :aria-current="(route.kind === 'discussion') ? 'page' : undefined" @click="openDiscussion(activeStoryId)"><MessageCircle :size="19"/><span>故事讨论</span></button>
        <button class="rail-button" aria-label="角色档案" :class="{ active: ['characters', 'character'].includes(route.kind) }" :aria-current="(['characters', 'character'].includes(route.kind)) ? 'page' : undefined" @click="openCharacters(activeStoryId)"><Users :size="19"/><span>角色档案</span></button>
        <button class="rail-button" aria-label="故事资料" :class="{ active: route.kind === 'materials' }" :aria-current="(route.kind === 'materials') ? 'page' : undefined" @click="workAction('materials')"><Settings :size="19"/><span>故事资料</span></button>
      </template>
      <div v-if="activeStoryId" class="rail-operations"><small>作品操作</small><button class="rail-button" aria-label="提示词设置" @click="workAction('prompts')"><Settings :size="19"/><span>提示词设置</span></button><button class="rail-button" aria-label="重建索引" @click="workAction('reindex')"><Search :size="19"/><span>重建索引</span></button><button class="rail-button danger" aria-label="移入回收站" @click="workAction('delete')"><Trash2 :size="19"/><span>移入回收站</span></button></div>
    </aside>
    <div class="route-content">
    <section v-if="route.kind === 'shelf'" class="library-page">
      <div class="shelf-heading"><div><span class="eyebrow">你的创作空间</span><h1>书架</h1><p class="muted">打开作品继续写作，或先准备自己的字段模板。</p></div><div class="shelf-template-actions"><n-button secondary @click="navigate('/#field-templates')">管理字段模板</n-button><n-button quaternary @click="toggleTrash">{{ trashOpen ? "关闭回收站" : "回收站" }}</n-button></div></div>
      <section v-if="trashOpen" class="trash-section">
        <div class="section-heading"><h2>回收站</h2><n-tag size="small" round>{{ trash.length + trashedDrafts.length }} 项</n-tag></div>
        <p class="muted">恢复可继续创作；永久删除会清理全部关联内容及运行历史，无法撤回。</p>
        <div v-if="trash.length || trashedDrafts.length" class="book-grid">
          <n-card v-for="book in trash" :key="book.id" size="small" class="book-card" :bordered="true">
            <div class="book-card-content"><span class="book-icon">回</span><div class="book-copy"><strong>{{ book.title }}</strong><small>小说 · {{ book.scene_count }} 幕</small></div></div>
            <div class="shelf-card-actions"><n-button size="small" :disabled="shelfBusy" @click="restoreStory(book.id)">恢复</n-button><n-button size="small" type="error" secondary :disabled="shelfBusy" @click="permanentlyDelete(book.id, book.title, false)">永久删除</n-button></div>
          </n-card>
          <n-card v-for="draft in trashedDrafts" :key="draft.id" size="small" class="book-card draft-book">
            <div class="book-card-content"><span class="book-icon">草</span><div class="book-copy"><strong>{{ draft.title || '新故事草稿' }}</strong><small>创作草稿 · 已移入回收站</small></div></div>
            <div class="shelf-card-actions"><n-button size="small" :disabled="shelfBusy" @click="setDraftDeleted(draft.id, false)">恢复</n-button><n-button size="small" type="error" secondary :disabled="shelfBusy" @click="permanentlyDelete(draft.id, draft.title, true)">永久删除</n-button></div>
          </n-card>
        </div><n-empty v-else description="回收站为空" />
      </section>
      <section class="book-section"><div class="section-heading"><h2>继续创作</h2><n-tag size="small" round :bordered="false">{{ stories.length }} 部作品</n-tag></div><div v-if="stories.length" class="book-grid"><n-card v-for="book in stories" :key="book.id" size="medium" class="book-card" hoverable @click="openStory(book.id)"><div class="book-card-content"><span class="book-icon"><BookOpen :size="17" /></span><div class="book-copy"><strong>{{ book.title }}</strong><small>{{ book.scene_count }} 幕 · 打开正文</small></div><span class="card-arrow">↗</span></div></n-card></div><div v-else class="library-empty"><n-empty description="书架还是空的"><template #extra><div class="empty-extra"><p>先构思一部作品，再从正文开始创作。</p><n-button type="primary" @click="navigate('/#new')"><template #icon><Plus :size="16"/></template>开始构思</n-button></div></template></n-empty></div></section>
      <section class="book-section"><div class="section-heading"><h2>创作草稿</h2><n-tag size="small" round :bordered="false">未创建为正式作品</n-tag></div><div v-if="drafts.length" class="book-grid"><n-card v-for="draft in drafts" :key="draft.id" size="medium" class="book-card draft-book" hoverable @click="openDraft(draft.id)"><div class="book-card-content"><span class="book-icon">草</span><div class="book-copy"><strong>{{ draft.title || "新故事草稿" }}</strong><small>构思草稿 · 继续完善</small></div><n-button size="small" type="error" quaternary :disabled="shelfBusy" @click.stop="setDraftDeleted(draft.id, true)">删除</n-button><span class="card-arrow">↗</span></div></n-card></div><p v-else class="muted">暂无创作草稿。新作品构思会自动保存到这里。</p></section>
      <p v-if="error" class="toast inline-toast" role="status">{{ error }}</p>
    </section>
    <FieldTemplatesPage v-else-if="route.kind === 'field-templates'" />
    <CreationPage v-else-if="route.kind === 'new' || route.kind === 'draft'" :key="route.kind === 'draft' ? route.id : 'new'" :draft-id="route.kind === 'draft' ? route.id : undefined" @back="showShelf" @created="openStory" />
    <Workbench v-else-if="route.kind === 'story'" :key="route.id" :story-id="route.id" @back="showShelf" @open-discussion="openDiscussion" @open-characters="openCharacters" />
    <DiscussionPage v-else-if="route.kind === 'discussion' && route.id" :key="route.id" :story-id="route.id" @back="openStory(route.id)" @open-story="openStory" />
    <MaterialsPage v-else-if="route.kind === 'materials'" :key="route.id" :story-id="route.id" />
    <CharacterPages v-else-if="route.kind === 'characters' || route.kind === 'character'" :key="routeKey" :mode="route.kind" />
    <main v-else class="empty-state"><h2>请从作品页面打开故事讨论</h2><button class="primary" @click="showShelf">返回书架</button></main>
    </div>
  </div>
  </n-message-provider></n-dialog-provider>
  </n-config-provider>
</template>

<style scoped>
.shelf-card-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}.shelf-template-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
</style>
