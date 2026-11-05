<script setup lang="ts">
import {computed,h,nextTick,onMounted,onUnmounted,reactive,ref,watch} from 'vue';
import {NAlert,NBadge,NButton,NCollapse,NCollapseItem,NDrawer,NDrawerContent,NDropdown,NEmpty,NFormItem,NInput,NModal,NSelect,NSkeleton,NTag,useDialog} from 'naive-ui';
import {ArrowLeft,BookOpen,ChevronDown,Check,FileText,Layers,LibraryBig,MessageCircle,MoreHorizontal,PanelLeft,Plus,Search,Settings2,Sparkles,Trash2,Users,Waypoints} from '@lucide/vue';
import {useWorkspace} from './useWorkspace';
import {useMobile} from './responsive';
import ChatPanel from './ChatPanel.vue';
import FieldsPanel from './FieldsPanel.vue';
import StagePanel from './StagePanel.vue';
import TemplateDrawer from './TemplateDrawer.vue';
import SettingsDrawer from './SettingsDrawer.vue';
import type {Entity,Novel} from '../studio-api';
const w=reactive(useWorkspace()),mobile=useMobile(),dialog=useDialog();
const tab=ref<'chat'|'stage'|'prose'|'fields'>('chat'),sidebar=ref(false),templates=ref(false),settings=ref(false),review=ref(false),trash=ref(false),search=ref('');
const naming=ref(false),name=ref(''),fieldsRef=ref<InstanceType<typeof FieldsPanel>|null>(null),drawerFieldsRef=ref<InstanceType<typeof FieldsPanel>|null>(null),fieldsDrawer=ref(false);
const savedError=ref(''),readerSize=ref(17),writingPanel=ref(false);
const readerSizes=[{label:'小字',value:15},{label:'标准',value:17},{label:'大字',value:20}];
const filteredBooks=computed(()=>w.books.filter(b=>b.deleted===trash.value&&b.title.toLowerCase().includes(search.value.toLowerCase())));
const drafts=computed(()=>filteredBooks.value.filter(b=>!b.created)),published=computed(()=>filteredBooks.value.filter(b=>b.created));
const archiveChapters=computed(()=>w.archive?w.novel?.branches.find(b=>b.id===w.archive)?.chapters??[]:w.novel?.chapters??[]);
const chapter=computed(()=>archiveChapters.value.find(c=>c.entity.id===w.scene?.chapter_id));
const activeTabLabel=computed(()=>({chat:w.novel?.created?'与 GM 讨论':'与 AI 沟通',stage:'角色演绎',prose:'小说正文',fields:'小说资料'}[tab.value]));
const currentError=computed(()=>w.error||(!w.novel?.busy&&w.novel?.error&&savedError.value!==`${w.novel.run_id}:${w.novel.error}`?w.novel.error:''));
const canFinish=computed(()=>!!chapter.value&&!!w.novel?.scenes.filter(s=>s.chapter_id===chapter.value?.entity.id).length&&w.novel.scenes.filter(s=>s.chapter_id===chapter.value?.entity.id).every(s=>s.confirmed&&!!s.text));
const actions=computed(()=>{
 if(!w.novel)return [];
 if(w.novel.deleted)return [{label:'恢复作品',key:'restore'},{label:'永久删除',key:'purge'}];
 return [{label:'修改书名',key:'rename',disabled:w.busy||!w.editable},{label:'字段模板',key:'templates'},{label:'上下文与提示词',key:'settings',disabled:w.busy},{type:'divider' as const,key:'divider'},
 ...(w.scene?[{label:'从此幕创建分支',key:'branch',disabled:w.busy},...(w.editable?[{label:'重置当前演绎',key:'reset',disabled:w.busy}]:[])]:[]),{label:'移入回收站',key:'delete',disabled:w.busy}];
});
function dismissError(){w.error='';savedError.value=`${w.novel?.run_id}:${w.novel?.error}`}
async function openBook(id:string){await w.open(id);location.hash='studio/'+id;tab.value='chat';sidebar.value=false}
async function create(){trash.value=false;await w.create();tab.value='chat';sidebar.value=false}
async function control(kind:string,extra:Record<string,unknown>={}){if(['new_scene','branch'].includes(kind)){tab.value='chat';sidebar.value=false}return await w.control(kind,extra)}
async function writingAction(kind:'write'|'undo_prose'|'finish_chapter'){
 const value=await w.control(kind,kind==='write'?{instruction:w.requirements}:{});
 if(!value)return;
 writingPanel.value=false;
 if(kind==='undo_prose')tab.value='chat';
}
function reset(){dialog.warning({title:'重新演绎这一幕？',content:'本次群聊、进出场和临时角色变化会被废弃。世界观、人物设定等编辑会保留。',positiveText:'重置演绎',negativeText:'保留当前演绎',onPositiveClick:()=>control('reset')})}
function remove(){dialog.warning({title:'移入回收站？',content:'正文、角色、演绎和分支都会保留，可以之后恢复。',positiveText:'移入回收站',negativeText:'取消',onPositiveClick:async()=>{await control('delete');w.home();trash.value=true}})}
function purge(){dialog.error({title:'永久删除这部作品？',content:'该书全部资料、对话、演绎、分支和正文都会被删除，无法恢复。',positiveText:'永久删除',negativeText:'取消',onPositiveClick:()=>w.purge()})}
function menu(key:string){if(key==='rename'){name.value=w.novel?.title??'';naming.value=true}else if(key==='templates')templates.value=true;else if(key==='settings')settings.value=true;else if(key==='reset')reset();else if(key==='delete')remove();else if(key==='purge')purge();else if(key==='branch')control('branch',{branch_id:w.archive});else if(key==='restore')control('restore')}
async function rename(){const value=await w.edit({scope:'creation',title:name.value});if(value)naming.value=false}
async function confirmCreation(){const value=await w.control('confirm_creation');if(value?.created){review.value=false;tab.value='chat'}}
async function applyTemplate(id:string){if(await w.applyTemplate(id))templates.value=false}
async function saveSettings(payload:Record<string,unknown>){if(await w.saveSettings(payload))settings.value=false}
function chooseScene(id:string){w.chooseScene(id);sidebar.value=false;tab.value='chat'}
function showFields(){if(mobile.value)tab.value='fields';else fieldsDrawer.value=true}
async function newCharacter(){showFields();await nextTick();(mobile.value?fieldsRef.value:drawerFieldsRef.value)?.newCharacter()}
function reviewFields(){review.value=false;showFields()}
function keyHash(){const id=location.hash.match(/^#studio\/(.+)$/)?.[1];if(id&&id!==w.novel?.id)openBook(id);else if(!id&&w.novel)w.home()}
// Loading a saved failure is not a new failure. Only later failures may raise a banner.
watch(()=>w.novel?.id,()=>{savedError.value=`${w.novel?.run_id}:${w.novel?.error}`;tab.value='chat'});
watch([tab,()=>w.scene?.entity.id,()=>w.archive],()=>{writingPanel.value=false});
onMounted(()=>{w.initialize();window.addEventListener('hashchange',keyHash)});
onUnmounted(()=>window.removeEventListener('hashchange',keyHash));
</script>
<template><div class="workspace-app" :class="{mobile}">
 <header class="app-header"><div class="header-leading"><NButton v-if="w.novel" quaternary circle aria-label="返回书架" @click="w.home"><template #icon><ArrowLeft :size="21"/></template></NButton><a v-else class="brand" href="#" @click.prevent="w.home"><span class="brand-mark">墨</span><span>墨间<small>让故事发生</small></span></a><div v-if="w.novel" class="header-book"><strong>{{w.novel.title}}</strong><small>{{w.novel.deleted?'回收站':w.novel.created?'角色演绎工作区':'构思草稿 · 尚未创建小说'}}</small></div></div><nav class="header-actions"><template v-if="!w.novel"><NButton quaternary :circle="mobile" @click="templates=true" aria-label="字段模板"><template #icon><Layers :size="19"/></template><span v-if="!mobile">字段模板</span></NButton><NButton quaternary :circle="mobile" @click="trash=!trash" :aria-label="trash?'返回书架':'回收站'"><template #icon><LibraryBig v-if="trash" :size="19"/><Trash2 v-else :size="19"/></template><span v-if="!mobile">{{trash?'书架':'回收站'}}</span></NButton><NButton type="primary" :loading="w.action==='create'" @click="create"><template #icon><Plus :size="18"/></template>创建小说</NButton></template><template v-else><NButton v-if="!w.novel.created&&!w.novel.deleted" type="primary" secondary size="small" :disabled="w.busy" @click="review=true">检查并建书</NButton><NButton v-if="w.novel.created" quaternary circle aria-label="目录与分支" @click="sidebar=true"><template #icon><PanelLeft :size="21"/></template></NButton><NDropdown trigger="click" :options="actions" @select="menu"><NButton quaternary circle aria-label="作品操作"><template #icon><MoreHorizontal :size="21"/></template></NButton></NDropdown></template><form v-if="!mobile&&!w.novel" method="post" action="/logout"><NButton quaternary attr-type="submit">退出登录</NButton></form></nav></header>
 <NAlert v-if="currentError" type="error" closable class="app-error" @close="dismissError">{{currentError}}</NAlert>
 <main v-if="!w.novel" class="library-scroll"><div class="library-content"><div class="library-heading"><div><span class="eyebrow">{{trash?'保留的作品与构思':'你的创作空间'}}</span><h1>{{trash?'回收站':'从一句想法，写到一个世界。'}}</h1><p>{{trash?'打开作品后可以恢复，或永久删除。':'先和 AI 聊聊人物与世界，再让角色在幕中自由行动。'}}</p></div><NInput v-model:value="search" clearable placeholder="搜索作品或构思" aria-label="搜索作品"><template #prefix><Search :size="17"/></template></NInput></div>
 <div v-if="w.loading" class="book-grid"><NSkeleton v-for="i in 3" :key="i" height="180px" :sharp="false"/></div>
 <template v-else><div v-if="!filteredBooks.length&&!trash&&!search" class="library-start"><div class="start-illustration"><BookOpen :size="44"/><span class="spark-one">✦</span><span class="spark-two">✦</span></div><h2>每个故事，都始于一次对话</h2><p>不必先填满设定。告诉 AI 你的想法，边聊边完善。</p><div><NButton type="primary" size="large" @click="create">开始构思</NButton><NButton secondary size="large" @click="templates=true">先看看字段模板</NButton></div></div><NEmpty v-else-if="!filteredBooks.length" :description="search?'没有找到匹配的作品':'回收站是空的'"/>
 <section v-if="drafts.length" class="book-section"><header><h2>{{trash?'构思草稿':'继续构思'}}<small>{{drafts.length}}</small></h2><p>这些想法还没有正式建书，继续聊下去。</p></header><div class="book-grid"><button v-for="book in drafts" :key="book.id" class="book-card draft-card" @click="openBook(book.id)"><div class="book-card-icon"><MessageCircle :size="25"/></div><span class="card-kind">构思草稿</span><h3>{{book.title}}</h3><span class="book-card-action">{{book.busy?'AI 正在运行':'继续与 AI 沟通'}} <ArrowLeft :size="15" class="right-arrow"/></span></button></div></section>
 <section v-if="published.length" class="book-section"><header><h2>我的小说<small>{{published.length}}</small></h2></header><div class="book-grid"><button v-for="book in published" :key="book.id" class="book-card" @click="openBook(book.id)"><div class="book-card-icon"><BookOpen :size="25"/></div><span class="card-kind">{{book.busy?'演绎运行中':'小说'}}</span><h3>{{book.title}}</h3><span class="book-card-action">进入创作 <ArrowLeft :size="15" class="right-arrow"/></span></button></div></section></template><footer class="library-footer"><button @click="templates=true">字段模板</button><span>设定可以慢慢完善，人物会找到自己的路。</span><form v-if="mobile" action="/logout" method="post"><NButton text attr-type="submit">退出登录</NButton></form></footer></div></main>
 <template v-else><div v-if="w.loading" class="workspace-loading"><NSkeleton text :repeat="6"/></div><div v-else class="work-area">
 <aside v-if="!mobile&&w.novel.created" class="desktop-directory"><div class="directory-heading"><span>章节与幕</span><NButton text size="small" aria-label="字段模板" @click="templates=true"><Layers :size="16"/></NButton></div><div class="directory-scroll"><NSelect :value="w.archive" @update:value="w.chooseArchive" :options="[{label:'当前路线',value:''},...w.novel.branches.map(b=>({label:b.name,value:b.id}))]" size="small" aria-label="写作路线"/><section v-for="c in archiveChapters" :key="c.entity.id" class="directory-chapter"><h3>{{c.entity.name}}</h3><button v-for="s in w.scenes.filter(s=>s.chapter_id===c.entity.id)" :key="s.entity.id" :class="['scene-link',{active:w.scene?.entity.id===s.entity.id}]" @click="chooseScene(s.entity.id)"><span class="scene-dot" :class="{confirmed:s.confirmed,written:!!s.text}"/><span>{{s.entity.name}}</span><small>{{s.text?'正文':s.confirmed?'确认':''}}</small></button></section></div><div class="directory-footer"><NButton block type="primary" secondary :disabled="w.busy||!!w.archive||!!w.novel.scenes.length&&!w.novel.scenes.at(-1)?.confirmed" @click="control('new_scene')"><template #icon><Plus :size="16"/></template>{{w.novel.scenes.length?'下一幕':'创建第一幕'}}</NButton><NButton block quaternary :disabled="w.busy||!!w.archive||!w.novel.chapters.at(-1)?.completed" @click="control('new_scene',{new_chapter:true})">开启新章节</NButton></div></aside>
 <section class="workspace-main"><div v-if="tab!=='prose'" class="workspace-toolbar"><div class="workspace-heading"><span class="eyebrow">{{w.novel.created?chapter?.entity.name??'准备开始':'先沟通构思，确认后再创建小说'}}</span><h1>{{w.novel.created?w.scene?.entity.name??'开始你的第一幕':'和 AI 一起构思'}}</h1></div><div class="workspace-tools"><NTag v-if="w.scene&&!w.editable" :bordered="false" size="small">{{w.scene.text?'已写正文':'已确认'}}</NTag><NButton v-if="!mobile" secondary :disabled="w.busy" @click="showFields">查看设定</NButton><NButton v-if="w.scene&&w.editable" type="primary" secondary :disabled="w.busy" :loading="w.action==='confirm_scene'" @click="control('confirm_scene')">确认这一幕</NButton><NButton v-else-if="w.scene&&!w.novel.deleted" secondary :disabled="w.busy" @click="control('branch',{branch_id:w.archive})"><template #icon><Waypoints :size="15"/></template>{{mobile?'分支修改':'从此幕创建分支'}}</NButton></div></div>
 <div v-if="w.novel.deleted" class="deleted-notice"><NAlert type="warning">这部作品在回收站中。<div class="modal-actions"><NButton @click="control('restore')">恢复作品</NButton><NButton text type="error" @click="purge">永久删除</NButton></div></NAlert></div>
 <div v-if="w.novel.created&&!w.scene&&tab!=='fields'" class="first-scene-empty"><div class="welcome-symbol"><Sparkles :size="28"/></div><h2>设定已就绪，开始第一幕</h2><p>每幕都有独立的讨论和角色演绎。先创建一幕，再和 GM 商量要发生什么。</p><NButton type="primary" size="large" :disabled="w.busy||w.novel.deleted" :loading="w.action==='new_scene'" @click="control('new_scene')">创建第一幕</NButton></div>
 <div v-else class="workspace-body" :class="{split:!mobile&&tab==='chat'&&w.novel.created}">
 <ChatPanel v-if="tab==='chat'" :key="w.novel.id+':'+(w.scene?.entity.id??'creation')+w.archive" :messages="w.messages" :can-change-turn="w.canChangeTurn" :legacy-turn="w.legacyTurn" :change-turn="w.changeTurn" :pending="w.optimistic" :draft="w.draft" @update:draft="w.draft=$event" :busy="w.novel.busy||w.action==='send'||w.action==='turn'" :editable="w.editable&&!w.action" :created="w.novel.created" :status="w.status" :tokens="w.preview?.input_tokens" :failed="w.failedSend" :poll-failed="w.pollFailed" :mobile="mobile" :calls="Math.max(0,w.novel.calls-w.roundStart)" @send="w.send()" @retry="w.send(w.failedSend)" @refresh="w.refresh().catch(()=>{w.pollFailed=true})" @settings="settings=true"/>
 <StagePanel v-if="w.scene&&(tab==='stage'||!mobile&&tab==='chat')" :scene="w.scene" :characters="w.novel.characters" :present="w.present" :offstage="w.offstage" :busy="w.busy" :editable="w.editable" :status="w.status" @enter="control('enter',{name:$event})" @leave="control('leave',{name:$event})" @create="newCharacter"/>
 <section v-if="tab==='prose'&&w.scene" class="prose-panel reader-panel" aria-label="小说正文">
  <div :key="w.archive+':'+w.scene.entity.id" class="prose-scroll reader-scroll" tabindex="0" aria-label="正文阅读区">
   <div class="reader-page">
    <header class="reader-heading">
     <p v-if="chapter">{{chapter.entity.name}}</p>
     <h1>{{w.scene.entity.name}}</h1>
    </header>
    <article v-if="w.scene.text" :style="{fontSize:readerSize+'px'}">{{w.scene.text}}</article>
    <NEmpty v-else :description="w.scene.confirmed?'这一幕还没有正文':'确认演绎后即可生成正文'">
     <template v-if="!w.scene.confirmed&&!w.archive&&!w.novel.deleted" #extra>
      <NButton secondary size="small" @click="tab='chat'">回到讨论</NButton>
     </template>
    </NEmpty>
    <NCollapse v-if="chapter?.summary" class="chapter-summary">
     <NCollapseItem :title="'章节摘要'+(chapter.summary_stale?' · 待更新':'')" name="summary"><p>{{chapter.summary}}</p></NCollapseItem>
    </NCollapse>
   </div>
  </div>
  <footer class="reader-footer">
   <span v-if="w.novel.running==='writing'" class="reader-status" role="status">{{w.scene.text?'正在重写正文…':'正在生成正文…'}}</span>
   <span v-else-if="w.novel.running==='summary'" class="reader-status" role="status">正在生成章节摘要…</span>
   <NSelect v-model:value="readerSize" size="small" :options="readerSizes" aria-label="正文字号"/>
   <NButton v-if="!w.archive&&!w.novel.deleted" secondary size="small" :disabled="!w.scene.confirmed||w.busy" @click="writingPanel=true">
    <template #icon><FileText :size="16"/></template>{{w.scene.text?'写作操作':'写正文'}}
   </NButton>
  </footer>
 </section>
 <div v-if="tab==='fields'" class="fields-scroll"><FieldsPanel ref="fieldsRef" :novel="w.novel" :scenes="w.scenes" :chapters="archiveChapters" :preview="w.preview" :editable="w.editable" :busy="w.busy" :save="w.edit"/></div>
 </div>
 <nav v-if="!mobile&&w.scene" class="desktop-view-tabs"><button v-for="item in [{key:'chat',label:'讨论与演绎'},{key:'prose',label:'小说正文'},{key:'fields',label:'小说资料'}]" :key="item.key" :class="{active:tab===item.key}" @click="tab=item.key as 'chat'|'prose'|'fields'">{{item.label}}</button><span>{{w.novel.calls}} 次调用 · {{w.novel.input_tokens.toLocaleString()}} 输入 / {{w.novel.output_tokens.toLocaleString()}} 输出 token</span></nav>
 <nav v-if="mobile" class="mobile-tabs" aria-label="工作区导航"><button :class="{active:tab==='chat'}" @click="tab='chat'" aria-label="对话"><MessageCircle :size="21"/><span>{{w.novel.created?'讨论':'对话'}}</span></button><button v-if="w.novel.created" :class="{active:tab==='stage'}" @click="tab='stage'" aria-label="演绎"><NBadge dot :show="w.novel.busy&&tab!=='stage'"><Users :size="21"/></NBadge><span>演绎</span></button><button v-if="w.novel.created" :class="{active:tab==='prose'}" @click="tab='prose'" aria-label="正文"><FileText :size="21"/><span>正文</span></button><button :class="{active:tab==='fields'}" @click="tab='fields'" aria-label="设定"><Layers :size="21"/><span>设定</span></button><button v-if="!w.novel.created" @click="templates=true" aria-label="模板"><LibraryBig :size="21"/><span>模板</span></button></nav>
 </section></div></template>
 <TemplateDrawer v-model:show="templates" :mobile="mobile" :novel="w.novel" :busy="w.busy" @apply="applyTemplate"/>
 <NDrawer v-if="tab==='prose'&&w.scene" v-model:show="writingPanel" placement="bottom" height="min(340px, calc(var(--app-height, 100dvh) - 12px))" :auto-focus="false" class="workspace-drawer">
  <NDrawerContent title="写作操作" closable>
   <div class="writing-sheet">
    <NFormItem label="本次正文要求" :show-feedback="false">
     <NInput v-model:value="w.requirements" type="textarea" :autosize="{minRows:2,maxRows:5}" :placeholder="w.scene.text?'可选，写下本次要调整的内容':'可选，写下篇幅、视角或文风要求'" :readonly="w.busy" :input-props="{'aria-label':'正文要求'}"/>
    </NFormItem>
    <div class="writing-sheet-actions">
     <NButton type="primary" :disabled="w.busy||!w.scene.confirmed||!!w.archive" :loading="w.action==='write'" @click="writingAction('write')">{{w.scene.text?'重写正文':'生成正文'}}</NButton>
     <NButton :disabled="w.busy||!w.scene.text||!!w.archive" @click="writingAction('undo_prose')">回退正文</NButton>
     <NButton secondary :disabled="w.busy||!canFinish||!!w.archive||!!chapter?.completed&&!chapter.summary_stale" :loading="w.action==='finish_chapter'" @click="writingAction('finish_chapter')">{{chapter?.completed&&!chapter.summary_stale?'本章已完成':'完成本章并生成摘要'}}</NButton>
    </div>
   </div>
  </NDrawerContent>
 </NDrawer>
 <SettingsDrawer v-if="w.novel" v-model:show="settings" :mobile="mobile" :novel="w.novel" :scene-id="w.scene?.entity.id??''" :archive-id="w.archive" :draft="w.draft" :busy="w.busy" :defaults="w.contracts?.prompts??{}" :tools="w.contracts?.tools??{}" @save="saveSettings"/>
 <NDrawer v-if="w.novel" v-model:show="fieldsDrawer" placement="right" :width="560" class="workspace-drawer"><NDrawerContent title="小说资料" closable :native-scrollbar="false"><FieldsPanel ref="drawerFieldsRef" :novel="w.novel" :scenes="w.scenes" :chapters="archiveChapters" :preview="w.preview" :editable="w.editable" :busy="w.busy" :save="w.edit"/></NDrawerContent></NDrawer>
 <NDrawer v-if="w.novel" v-model:show="sidebar" :placement="mobile?'bottom':'left'" :width="mobile?undefined:320" :height="mobile?'min(76%, 640px)':undefined" class="workspace-drawer"><NDrawerContent title="目录与分支" closable :native-scrollbar="false"><NSelect :value="w.archive" @update:value="w.chooseArchive" :options="[{label:'当前路线',value:''},...w.novel.branches.map(b=>({label:b.name,value:b.id}))]" aria-label="写作路线"/><section v-for="c in archiveChapters" :key="c.entity.id" class="directory-chapter"><h3>{{c.entity.name}}</h3><button v-for="s in w.scenes.filter(s=>s.chapter_id===c.entity.id)" :key="s.entity.id" :class="['scene-link',{active:w.scene?.entity.id===s.entity.id}]" @click="chooseScene(s.entity.id)"><span class="scene-dot" :class="{confirmed:s.confirmed,written:!!s.text}"/><span>{{s.entity.name}}</span><small>{{s.text?'已有正文':s.confirmed?'已确认':'演绎中'}}</small></button></section><template #footer><div class="drawer-footer-stack"><NButton block type="primary" :disabled="w.busy||!!w.archive||!!w.novel.scenes.length&&!w.novel.scenes.at(-1)?.confirmed" @click="control('new_scene')">{{w.novel.scenes.length?'创建下一幕':'创建第一幕'}}</NButton><NButton block :disabled="w.busy||!!w.archive||!w.novel.chapters.at(-1)?.completed" @click="control('new_scene',{new_chapter:true})">开启新章节</NButton><form action="/logout" method="post"><NButton block quaternary attr-type="submit" :disabled="w.busy">退出登录</NButton></form></div></template></NDrawerContent></NDrawer>
 <NDrawer v-if="w.novel" v-model:show="review" :placement="mobile?'bottom':'right'" :width="mobile?undefined:520" :height="mobile?'calc(var(--app-height,100dvh) - 20px)':undefined" class="workspace-drawer"><NDrawerContent title="检查设定，准备建书" closable :native-scrollbar="false"><NAlert type="info" :show-icon="false">确认后才会创建正式小说，之前的内容会作为构思草稿保留。</NAlert><div class="review-book"><h2>{{w.novel.title}}</h2><span>字段模板 · {{w.novel.field_template_name}}</span><NButton text type="primary" @click="review=false;templates=true">选择字段模板</NButton></div><section v-for="(label,key) in {world:'世界观',characters:'角色',creation:'创作要求'}" :key="key" class="review-section"><h3>{{label}}</h3><p v-if="!w.novel[key].length" class="muted">暂未设定，可以之后继续补充。</p><article v-for="e in w.novel[key]" :key="e.id"><strong>{{e.name}}</strong><p v-for="d in e.description" :key="d.name">{{d.name}}：{{d.value.join('；')||'待补充'}}</p><p v-for="a in e.special_attributes" :key="a.key">{{a.key}}：{{String(a.current_value)}}</p></article></section><NButton secondary block @click="reviewFields">继续检查或编辑资料</NButton><template #footer><NButton block type="primary" size="large" :disabled="w.busy||!w.canConfirm" :loading="w.action==='confirm_creation'" @click="confirmCreation">确认设定并创建小说</NButton><small v-if="!w.canConfirm" class="footer-hint">先和 AI 完成一轮对话，再确认建书。</small></template></NDrawerContent></NDrawer>
 <NModal v-model:show="naming" preset="card" title="修改书名" :style="{width:'min(420px, calc(100vw - 28px))'}"><NFormItem label="书名"><NInput v-model:value="name" :input-props="{'aria-label':'书名'}" @keydown.enter="rename"/></NFormItem><template #footer><div class="modal-actions"><NButton @click="naming=false">取消</NButton><NButton type="primary" :loading="w.action==='edit'" @click="rename">保存</NButton></div></template></NModal>
</div></template>

<style scoped>
.reader-panel{padding:0;background:#faf8f2}
.reader-scroll{padding:clamp(26px,5vw,56px) clamp(20px,5vw,48px) 48px}
.reader-page{max-width:42rem;margin-inline:auto}
.reader-heading{text-align:center;margin-bottom:clamp(28px,5vw,44px)}
.reader-heading p{margin:0 0 10px;color:#746a5d;font-size:12px;letter-spacing:.1em}
.reader-heading h1{margin:0;color:#302820;font:600 clamp(22px,4vw,30px)/1.5 'Songti SC',SimSun,serif}
.reader-scroll article{color:#383029;font-family:'Songti SC','Noto Serif SC',SimSun,Georgia,serif;line-height:2.15}
.reader-footer{display:flex;flex-wrap:wrap;justify-content:flex-end;align-items:center;gap:8px;padding:7px clamp(16px,4vw,34px);border-top:1px solid #e8e1d4;background:#fdfbf7}
.reader-footer .n-select{width:5rem}
.reader-status{margin-right:auto;color:#746a5d;font-size:12px}
.writing-sheet{max-width:42rem;margin-inline:auto}
.writing-sheet-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.writing-sheet :deep(textarea){font-size:16px}
@media(max-width:520px){
 .reader-scroll{padding-inline:20px}
 .reader-footer .n-button{min-height:36px}
 .writing-sheet-actions .n-button{min-height:40px}
}
</style>
