export type Visibility = 0 | 1 | 2;
export type Scope = 'world' | 'characters' | 'creation' | 'chapters' | 'scenes';
export type Attribute = { key:string; value_type:'str'|'int'|'float'|'bool'|'list'; current_value:unknown; why:string; how:string; when:string; visibility:Visibility };
export type Description = { name:string; value:string[]; visibility:Visibility };
export type Entity = { id:string; name:string; type:string; description:Description[]; special_attributes:Attribute[] };
export type EntityInput = Omit<Entity,'id'>;
export type TemplateFields = Record<Scope,EntityInput>;
export type FieldTemplate = {id:string;version:number;name:string;fields:TemplateFields};
export type StageEvent = { id:string; kind:string; actor_id:string; text:string; audience:string[] };
export type Message = { id:string; role:string; content:string; checkpointed?:boolean; changes?:{paragraph:number|null;before:string;after:string;count:number}[] };
export type Scene = { entity:Entity; chapter_id:string; confirmed:boolean; messages:Message[]; writing_messages:Message[]; events:StageEvent[]; abandoned:StageEvent[][]; text:string };
export type Chapter = { entity:Entity; summary:string; summary_stale:boolean; completed:boolean };
export type Compression = { full_chapters:number; scene_chapters:number; snapshot_interval:number; max_calls:number };
export type Novel = { turn_checkpoint:{scene_id:string;message_id:string;version:number;channel?:'discussion'|'writing'}|null; id:string; version:number; title:string; created:boolean; deleted:boolean; template_fields:TemplateFields; field_template_name:string; world:Entity[]; characters:Entity[]; creation:Entity[]; chapters:Chapter[]; scenes:Scene[]; branches:{id:string;name:string;scenes:Scene[];chapters:Chapter[]}[]; creation_messages:Message[]; compression:Compression; prompts:Record<string,string>; context_source:string; busy:boolean; run_id:string; running:string; error:string; calls:number; input_tokens:number; output_tokens:number };
export type Card = Pick<Novel,'id'|'title'|'created'|'deleted'|'busy'>;
export type Preview = { input_tokens:number; method:string; characters:Record<string,Entity>; present:string[]; messages:{role:string;content:string}[]; tools:unknown[] };
export async function studio<T>(path:string, data?:unknown, method = data === undefined ? 'GET' : 'POST'):Promise<T> {
 const response=await fetch('/api/studio'+path,{method,cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});
 let body:unknown;
 try{body=await response.json()}catch{throw new Error(`服务响应异常（${response.status}），请重试`)}
 if(response.status===401){window.location.assign('/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));throw new Error('请先输入访问密码')}
 if(!response.ok){const detail=body&&typeof body==='object'&&'detail' in body?body.detail:null;const message=typeof detail==='string'?detail:Array.isArray(detail)?detail.map(item=>item&&typeof item==='object'&&'msg' in item?String(item.msg):String(item)).join('；'):`请求失败（${response.status}），请重试`;throw new Error(message)}
 return body as T;
}
