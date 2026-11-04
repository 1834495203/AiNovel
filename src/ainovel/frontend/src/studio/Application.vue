<script setup lang="ts">
import {computed} from 'vue';
import {NConfigProvider,NDialogProvider,NMessageProvider,dateZhCN,zhCN,type GlobalThemeOverrides} from 'naive-ui';
import WorkspaceApp from './WorkspaceApp.vue';
import './workspace.css';
const theme:GlobalThemeOverrides={
 common:{primaryColor:'#5b63ce',primaryColorHover:'#7078df',primaryColorPressed:'#464fb8',primaryColorSuppl:'#5b63ce',borderRadius:'10px',fontFamily:"Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif",textColorBase:'#242a3d',fontSize:'14px'},
 Button:{fontWeight:'550',borderRadiusMedium:'10px',borderRadiusSmall:'8px'},
 Input:{borderRadius:'10px'},Select:{peers:{InternalSelection:{borderRadius:'10px'}}},
 Drawer:{borderRadius:'18px'},Card:{borderRadius:'16px'},
};
</script>
<template><NConfigProvider :theme-overrides="theme" :locale="zhCN" :date-locale="dateZhCN"><NDialogProvider><NMessageProvider placement="top" :max="3"><WorkspaceApp/></NMessageProvider></NDialogProvider></NConfigProvider></template>
