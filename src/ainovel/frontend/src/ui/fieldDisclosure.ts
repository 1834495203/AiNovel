import type { InjectionKey } from "vue";

// One map per editor tree lets bulk actions reach children that are currently hidden.
export const fieldDisclosureKey: InjectionKey<Map<string, boolean>> = Symbol("field-disclosure");
