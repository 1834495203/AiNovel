export type Visibility = 0 | 1 | 2;
export type Scope = 'world' | 'characters' | 'creation' | 'chapters';
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
export type Novel = { turn_checkpoint:{scene_id:string;message_id:string;version:number;channel?:'discussion'|'writing'}|null; id:string; version:number; title:string; created:boolean; deleted:boolean; template_fields:TemplateFields; field_template_name:string; world:Entity[]; characters:Entity[]; creation:Entity[]; chapters:Chapter[]; scenes:Scene[]; branches:{id:string;name:string;scenes:Scene[];chapters:Chapter[]}[]; creation_messages:Message[]; compression:Compression; prompts:Record<string,string>; documentation?:StudioDocumentation; context_source:string; gm_context_source?:string|null; gm_templates?:ContextTemplate[]; active_gm_template_id?:string|null; active_gm_template?:ContextTemplate|null; context_template_sets?:Partial<Record<'role'|'writing',ContextTemplateSet>>; busy:boolean; run_id:string; running:string; error:string; calls:number; input_tokens:number; output_tokens:number };
export type Card = Pick<Novel,'id'|'title'|'created'|'deleted'|'busy'>;
export type Preview = { input_tokens:number; method:string; characters:Record<string,Entity>; present:string[]; messages:{role:string;content:string}[]; tools:unknown[]; mode?:string; rendered_context?:string };
export type ContextMode = 'gm'|'role'|'writing';
export type StudioMode = ContextMode|'creation'|'writing_chat'|'summary';
export type ContextSection = 'template'|'behavior'|'documentation'|'compression';
export type VariableDoc = {type:string;description:string};
export type ToolDoc = {name:string;description:string;parameters:ContextSchema};
export type DocumentationSettings = {tool_descriptions:Record<string,string>;variable_descriptions:Record<string,string>;variable_detail:'compact'|'full'|'off'};
export type StudioDocumentation = Partial<Record<StudioMode,DocumentationSettings>>;
export type StudioSettings = {compression:Compression;prompts:Record<string,string>;context_source:string;documentation:StudioDocumentation};
export type StudioContracts = {prompts:Record<string,string>;tools:Record<StudioMode,ToolDoc[]>;context_templates:ContextTemplateCatalog;
 variable_docs:Record<StudioMode,Record<string,VariableDoc>>;shared_gm_variable_docs:Record<string,VariableDoc>};
export type ContextTemplateDraft = {name:string;source:string;schema_version:'gm-v1'|'shared-v1'|'role-v1'|'writing-v1'};
export type ContextTemplate = ContextTemplateDraft & {id:string};
export type ContextTemplateSet = {templates:ContextTemplate[];active_id:string|null;active:ContextTemplate|null};
export type ContextTemplateCatalog = Record<ContextMode,{default:ContextTemplate;schema:ContextSchema}>;
export type ContextDrafts = Record<ContextMode,Record<string,ContextTemplateDraft>>;
export type ContextTemplateRender = {rendered_context:string;input_tokens:number;context_tokens:number;
 documentation:{tool_descriptions?:Record<string,string>;variable_schema?:Record<string,VariableDoc>;tool_result_schema:ContextSchema}};
export type ContextTemplatePreview = ContextTemplateRender & {sample:boolean;active:ContextTemplateRender|null;active_error:string};
export type ContextSchema = {
 type?:string;description?:string;properties?:Record<string,ContextSchema>;
 items?:ContextSchema;anyOf?:ContextSchema[];enum?:string[];$ref?:string;$defs?:Record<string,ContextSchema>;
};
export async function studio<T>(path:string, data?:unknown, method = data === undefined ? 'GET' : 'POST'):Promise<T> {
 const response=await fetch('/api/studio'+path,{method,cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},...(data===undefined?{}:{body:JSON.stringify(data)})});
 let body:unknown;
 try{body=await response.json()}catch{throw new Error(`服务响应异常（${response.status}），请重试`)}
 if(response.status===401){window.location.assign('/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));throw new Error('请先输入访问密码')}
 if(!response.ok){const detail=body&&typeof body==='object'&&'detail' in body?body.detail:null;const message=typeof detail==='string'?detail:Array.isArray(detail)?detail.map(item=>item&&typeof item==='object'&&'msg' in item?String(item.msg):String(item)).join('；'):`请求失败（${response.status}），请重试`;throw new Error(message)}
 return body as T;
}
