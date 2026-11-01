import type { Schemas } from "../api/client";
import { reactive } from "vue";
type Definition = Schemas["FieldDefinition-Input"];
type Entry = Schemas["Entry-Input"];

const defaultFieldHelp = reactive(new Map<string, string>());
export function updateDefaultFieldHelp(templates: Schemas["FieldTemplate-Output"][]) {
  function visit(fields: Schemas["FieldDefinition-Output"][]) {
    for (const field of fields) {
      if (field.id && fieldDescription(field.description)) defaultFieldHelp.set(field.id, field.description);
      visit(field.children ?? []);
      if (field.item) visit([field.item]);
    }
  }
  defaultFieldHelp.clear();
  templates.filter(template => template.builtin).forEach(template => visit(template.fields ?? []));
}

export function fieldDescription(description?: string | null, definitionId?: string): string {
  const value = description ?? "";
  return /^\s*填写\s*.+?；(?:可在自定义模板中修改解释|可按作品需要修改定义)。\s*$/.test(value)
    ? (definitionId ? defaultFieldHelp.get(definitionId) ?? "" : "") : value;
}

export const fieldScopeLabels: Record<Schemas["FieldTarget"]["scope"], string> = {
  story: "小说资料", world: "世界观", character: "角色档案", state: "角色状态",
  preferences: "创作偏好", writer: "创作工作区", chapter: "章节", scene: "幕",
};

export function completeTemplates(templates: Schemas["FieldTemplate-Input"][]): Schemas["FieldTemplate-Input"][] {
  return Object.entries(fieldScopeLabels).map(([scope, name]) => {
    const existing = templates.find(item => item.scope === scope);
    return existing ? JSON.parse(JSON.stringify(existing)) as Schemas["FieldTemplate-Input"] : {
      id: crypto.randomUUID(), name, description: "", scope: scope as Schemas["FieldTarget"]["scope"], fields: [], builtin: false,
    };
  });
}

export function instantiateField(definition: Definition): Entry {
  return { id: crypto.randomUUID(), name: definition.name, value: null, description: "", reason: "用户填写字段", definition,
    children: (definition.children ?? []).map(instantiateField) };
}

export function fieldDefinition(entry: Entry): Definition {
  const definition: Definition = entry.definition ? JSON.parse(JSON.stringify(entry.definition)) : { id: entry.id ?? crypto.randomUUID(), name: entry.name, description: "", type: entry.children?.length ? "object" :
    typeof entry.value === "boolean" ? "boolean" : typeof entry.value === "number" ? "number" : "text",
    children: entry.children?.map(fieldDefinition) ?? [], options: [], required: false, allow_extra: true };
  definition.name = entry.name;
  if (definition.type === "object") definition.children = (entry.children ?? []).map(fieldDefinition);
  if (definition.type === "list" && definition.item && entry.children?.length) {
    const item = fieldDefinition(entry.children[0]); item.name = definition.item.name; definition.item = item;
  }
  return definition;
}
