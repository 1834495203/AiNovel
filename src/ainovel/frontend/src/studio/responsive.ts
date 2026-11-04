import { onBeforeUnmount, onMounted, ref } from 'vue';
export function useMobile(){
  const mobile=ref(window.innerWidth<900);
  function resize(){mobile.value=window.innerWidth<900;const v=window.visualViewport;document.documentElement.style.setProperty('--app-height',`${v?.height??window.innerHeight}px`);document.documentElement.style.setProperty('--viewport-top',`${v?.offsetTop??0}px`)}
  onMounted(()=>{resize();window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);window.visualViewport?.addEventListener('scroll',resize)});
  onBeforeUnmount(()=>{window.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('scroll',resize)});
  return mobile;
}
