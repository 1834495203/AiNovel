<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref, watchEffect} from 'vue';
import {NConfigProvider,NDialogProvider,NMessageProvider,darkTheme,dateZhCN,zhCN,type GlobalThemeOverrides} from 'naive-ui';
import WorkspaceApp from './WorkspaceApp.vue';
import './workspace.css';
const themeKey = 'ainovel-workspace-theme';
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
const systemDark = ref(systemTheme.matches);
const preference = ref<'light'|'dark'|null>(null);
try {
 const saved = localStorage.getItem(themeKey);
 if (saved === 'light' || saved === 'dark') preference.value = saved;
} catch { /* The switch still works when browser storage is unavailable. */ }
const dark = computed(() => preference.value === null ? systemDark.value : preference.value === 'dark');

function updateSystemTheme(event:MediaQueryListEvent){systemDark.value = event.matches}
onMounted(() => systemTheme.addEventListener('change', updateSystemTheme));
onBeforeUnmount(() => systemTheme.removeEventListener('change', updateSystemTheme));
function toggleTheme(){
 const next = dark.value ? 'light' : 'dark';
 preference.value = next;
 try { localStorage.setItem(themeKey, next); } catch { /* Keep the current choice in memory. */ }
}
watchEffect(() => {
 document.documentElement.dataset.theme = dark.value ? 'dark' : 'light';
 document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark.value ? '#141822' : '#f7f8fc');
});

const theme = computed<GlobalThemeOverrides>(() => ({
 common:{
  ...(dark.value
   ? {primaryColor:'#a9b3ff',primaryColorHover:'#c1c8ff',primaryColorPressed:'#909cf1',primaryColorSuppl:'#a9b3ff',textColorBase:'#e6eaf5'}
   : {primaryColor:'#5b63ce',primaryColorHover:'#7078df',primaryColorPressed:'#464fb8',primaryColorSuppl:'#5b63ce',textColorBase:'#242a3d'}),
  borderRadius:'10px',fontFamily:"Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif",fontSize:'14px',
 },
 Button:{fontWeight:'550',borderRadiusMedium:'10px',borderRadiusSmall:'8px'},
 Input:{borderRadius:'10px'},Select:{peers:{InternalSelection:{borderRadius:'10px'}}},
 Drawer:{borderRadius:'18px'},Card:{borderRadius:'16px'},
}));
</script>
<template><NConfigProvider :theme="dark ? darkTheme : null" :theme-overrides="theme" :locale="zhCN" :date-locale="dateZhCN"><NDialogProvider><NMessageProvider placement="top" :max="3"><WorkspaceApp :dark="dark" @toggle-theme="toggleTheme"/></NMessageProvider></NDialogProvider></NConfigProvider></template>
