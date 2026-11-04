import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useMessage } from 'naive-ui';
import { localId } from './localId';
import { studio, type Card, type Entity, type Novel, type Preview, type Scene } from '../studio-api';

export function useWorkspace() {
  const toast = useMessage();
  const books = ref<Card[]>([]), novel = ref<Novel|null>(null), preview = ref<Preview|null>(null);
  const selected = ref(''), archive = ref(''), draft = ref(''), requirements = ref('');
  const loading = ref(false), action = ref(''), error = ref(''), failedSend = ref('');
  const optimistic = ref<{id:string;content:string;before:number}|null>(null);
  const roundStart = ref(0), pollFailed = ref(false);
  const contracts = ref<{prompts:Record<string,string>;tools:Record<string,unknown[]>}>();
  const scenes = computed(()=>archive.value ? novel.value?.branches.find(b=>b.id===archive.value)?.scenes??[] : novel.value?.scenes??[]);
  const scene = computed(()=>scenes.value.find(s=>s.entity.id===selected.value)??scenes.value.at(-1));
  const messages = computed(()=>novel.value?.created ? scene.value?.messages??[] : novel.value?.creation_messages??[]);
  const busy = computed(()=>!!action.value || !!novel.value?.busy);
  const editable = computed(()=>!!novel.value && !novel.value.deleted && !archive.value && (!novel.value.created || !!scene.value && scene.value.entity.id===novel.value.scenes.at(-1)?.entity.id && !scene.value.confirmed && !scene.value.text));
  const canConfirm = computed(()=>!!novel.value?.creation_messages.some(m=>m.role==='user') && !!novel.value?.creation_messages.some(m=>m.role==='assistant'));
  const lastUser = computed(()=>[...messages.value].reverse().find(m=>m.role==='user'));
  const legacyTurn = computed(()=>!!lastUser.value&&!novel.value?.turn_checkpoint);
  const canChangeTurn = computed(()=>editable.value&&!busy.value&&!!lastUser.value&&(legacyTurn.value||novel.value?.turn_checkpoint?.message_id===lastUser.value.id&&novel.value.turn_checkpoint.scene_id===(scene.value?.entity.id??'')&&novel.value.turn_checkpoint.version===novel.value.version));
  const present = computed(()=> (preview.value?.present??[]).map(id=>(preview.value?.characters[id]??novel.value?.characters.find(c=>c.id===id))).filter(Boolean) as Entity[]);
  const offstage = computed(()=>novel.value?.characters.filter(c=>!preview.value?.present.includes(c.id))??[]);
  const status = computed(()=>{
    if(action.value==='send')return '正在发送你的消息';
    const value=novel.value?.running??'';
    const actor=value.split(' · ')[1];
    if(value.startsWith('role'))return actor?`${actor} 正在决定如何行动`:'角色正在行动';
    return ({gm:'GM 正在推进演绎',creation:'AI 正在整理你的构思',writing:'正在生成正文',summary:'正在整理章节摘要'} as Record<string,string>)[value]??(action.value?'正在保存':'等待你的下一句话');
  });
  let disposed=false, polling=false, estimateSeq=0, routeSeq=0;
  let estimateTimer=0, timer=0;
  const cachedDrafts=new Map<string,string>();
  const draftKey=computed(()=>`${novel.value?.id??''}:${scene.value?.entity.id??'creation'}:${archive.value}`);
  watch(draftKey,(key,old)=>{if(old)cachedDrafts.set(old,draft.value);draft.value=cachedDrafts.get(key)??'';failedSend.value='';optimistic.value=null});

  function setNovel(value:Novel) {
    if(novel.value?.id===value.id && value.version<novel.value.version)return;
    novel.value=value;
    const list=value.created ? value.scenes.find(s=>s.entity.id===scene.value?.entity.id)?.messages??[] : value.creation_messages;
    if(optimistic.value && list.slice(optimistic.value.before).some(m=>m.role==='user'&&m.content===optimistic.value?.content)) optimistic.value=null;
    if(!value.busy && !action.value)optimistic.value=null;
  }
  async function list(){books.value=await studio<Card[]>('/novels')}
  async function inspect(){
    const id=novel.value?.id;if(!id)return;
    const seq=++estimateSeq;
    const value=await studio<Preview>(`/novels/${id}/preview?scene_id=${encodeURIComponent(scene.value?.entity.id??'')}&branch_id=${encodeURIComponent(archive.value)}&message=${encodeURIComponent(draft.value)}`);
    if(seq===estimateSeq && novel.value?.id===id)preview.value=value;
  }
  async function refresh(){
    const id=novel.value?.id;if(!id)return;
    const value=await studio<Novel>('/novels/'+id);
    if(novel.value?.id!==id)return;
    setNovel(value);pollFailed.value=false;
    await inspect();
    if(!value.busy)await list();
  }
  async function run<T>(name:string,fn:()=>Promise<T>,success?:string):Promise<T|undefined>{
    if(action.value)return;
    action.value=name;error.value='';
    try{const value=await fn();if(success)toast.success(success);return value}
    catch(e){error.value=e instanceof Error?e.message:String(e);toast.error(error.value);return undefined}
    finally{action.value=''}
  }
  async function open(id:string){
    const seq=++routeSeq;loading.value=true;error.value='';
    try{const value=await studio<Novel>('/novels/'+id);if(seq!==routeSeq)return;archive.value='';selected.value=value.scenes.at(-1)?.entity.id??'';novel.value=value;preview.value=null;roundStart.value=value.calls;await inspect()}
    catch(e){error.value=String(e)}finally{if(seq===routeSeq)loading.value=false}
  }
  async function create(){await run('create',async()=>{const value=await studio<Novel>('/novels',{});archive.value='';selected.value='';novel.value=value;location.hash='studio/'+value.id;await list();await inspect()})}
  function home(){++routeSeq;novel.value=null;preview.value=null;location.hash='';list().catch(()=>{})}
  function chooseScene(id:string){selected.value=id;preview.value=null;inspect().catch(()=>{})}
  function chooseArchive(id:string){archive.value=id;selected.value=scenes.value.at(-1)?.entity.id??'';preview.value=null;inspect().catch(()=>{})}
  async function control(kind:string,extra:Record<string,unknown>={}){
    return run(kind,async()=>{
      const n=novel.value;if(!n)throw new Error('请先选择作品');
      const value=await studio<Novel>(`/novels/${n.id}/control`,{version:n.version,action:kind,scene_id:scene.value?.entity.id??'',...extra});
      if(['branch','new_scene','undo_prose'].includes(kind)){archive.value='';selected.value=value.scenes.at(-1)?.entity.id??''}
      setNovel(value);await inspect();await list();return value;
    },({confirm_creation:'小说已创建，可以开始第一幕了',confirm_scene:'这一幕已确认',reset:'演绎已重置，设定保留',branch:'新分支已就绪，原路线已保留',delete:'已移入回收站',restore:'已恢复'} as Record<string,string>)[kind]);
  }
  async function send(text=draft.value){
    if(!text.trim()||busy.value||!editable.value||!novel.value)return;
    const n=novel.value, id=n.id, sceneId=scene.value?.entity.id??'';
    const before=messages.value.length;const content=text.trim();
    optimistic.value={id:localId('pending'),content,before};draft.value='';failedSend.value='';roundStart.value=n.calls;
    await run('send',async()=>{
      try{
        const value=await studio<Novel>(`/novels/${id}/message`,{version:n.version,scene_id:sceneId,message:content});
        if(novel.value?.id===id)setNovel(value);
      }catch(e){
        // A lost response may still have been committed. Check before offering a retry.
        let accepted=false;
        try{const value=await studio<Novel>('/novels/'+id);const list=sceneId?value.scenes.find(s=>s.entity.id===sceneId)?.messages??[]:value.creation_messages;
          accepted=list.slice(before).some(m=>m.role==='user'&&m.content===content);if(novel.value?.id===id)setNovel(value)}catch{}
        if(!accepted){optimistic.value=null;draft.value=content;failedSend.value=content;throw e}
      }
    });
    refresh().catch(()=>{pollFailed.value=true});
  }
  async function edit(payload:Record<string,unknown>){return run('edit',async()=>{if(!novel.value)throw new Error('请选择作品');const value=await studio<Novel>(`/novels/${novel.value.id}/edit`,{version:novel.value.version,...payload});setNovel(value);await inspect();await list();return value},'资料已保存')}
  async function changeTurn(kind:'retry'|'edit'|'withdraw',content=''){
    if(!canChangeTurn.value||!novel.value||!lastUser.value)return false;
    const n=novel.value,id=n.id,message=lastUser.value,preserveState=legacyTurn.value;
    roundStart.value=n.calls;
    const value=await run('turn',async()=>{
      const updated=await studio<Novel>(`/novels/${id}/turn`,{version:n.version,action:kind,scene_id:scene.value?.entity.id??'',message_id:message.id,message:content,preserve_state:preserveState});
      setNovel(updated);failedSend.value='';optimistic.value=null;
      if(kind==='withdraw'&&!draft.value.trim())draft.value=message.content;
      await inspect();await list();return updated;
    },kind==='withdraw'?(preserveState?'已撤回消息，当前资料和演绎保留':'已撤回本轮对话及操作'):undefined);
    return !!value;
  }
  async function applyTemplate(id:string){return run('template',async()=>{if(!novel.value)throw new Error('请选择作品');const value=await studio<Novel>(`/novels/${novel.value.id}/template`,{version:novel.value.version,template_id:id});setNovel(value);await inspect();return value},'字段已初始化，本书会独立保存')}
  async function saveSettings(payload:Record<string,unknown>){return run('settings',async()=>{if(!novel.value)throw new Error('请选择作品');const value=await studio<Novel>(`/novels/${novel.value.id}/settings`,{version:novel.value.version,...payload});setNovel(value);await inspect();return value},'设置已保存')}
  async function purge(){return run('purge',async()=>{if(!novel.value)return;await studio(`/novels/${novel.value.id}?version=${novel.value.version}`,undefined,'DELETE');home();await list()},'作品已永久删除')}
  watch(draft,()=>{clearTimeout(estimateTimer);estimateTimer=window.setTimeout(()=>{if(!novel.value?.busy)inspect().catch(()=>{})},450)});
  async function tick(){if(disposed)return;try{if(novel.value?.busy&&!action.value&&!polling){polling=true;await refresh()}}catch{pollFailed.value=true}finally{polling=false;if(!disposed)timer=window.setTimeout(tick,700)}}
  async function initialize(){loading.value=true;try{const [catalog]=await Promise.all([studio<typeof contracts.value>('/contracts'),list()]);contracts.value=catalog;const id=location.hash.match(/^#studio\/(.+)$/)?.[1];if(id)await open(id)}catch(e){error.value=String(e)}finally{loading.value=false}tick()}
  onBeforeUnmount(()=>{disposed=true;clearTimeout(timer);clearTimeout(estimateTimer)});
  return {books,novel,preview,selected,archive,draft,requirements,loading,action,error,failedSend,optimistic,roundStart,pollFailed,contracts,
    scenes,scene,messages,busy,editable,canConfirm,lastUser,legacyTurn,canChangeTurn,changeTurn,present,offstage,status,list,inspect,refresh,run,open,create,home,chooseScene,chooseArchive,control,send,edit,applyTemplate,saveSettings,purge,initialize};
}

export type Workspace=ReturnType<typeof useWorkspace>;
