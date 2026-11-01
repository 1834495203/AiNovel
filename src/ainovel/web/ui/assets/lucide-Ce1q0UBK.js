import{i as L,D as M}from"./vue-Q-J874vY.js";/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const h={xmlns:"http://www.w3.org/2000/svg",width:24,height:24,viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":2,"stroke-linecap":"round","stroke-linejoin":"round"};/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const S=(...t)=>t.filter((e,a,o)=>!!e&&e.trim()!==""&&o.indexOf(e)===a).join(" ").trim();/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */function _(t){return t!=null}function P(t,e={}){var g,m;const a=e.attributeNames??{},o=n=>a[n]??n,c=t.size??t.width??h.width,l=t.size??t.height??h.height,u=((g=t.aliases)==null?void 0:g.filter(n=>typeof n=="string"&&n.trim()!=="").map(n=>`lucide-${n}`))??[],k=[...t.name?[`lucide-${t.name}`]:[],...u],y=((m=e.className)==null?void 0:m.split(" ").filter(Boolean))??[],f=e.includeDefaultClasses===!1?S(...y):S("lucide",...k,...y),r=e.absoluteStrokeWidth?Number(e.strokeWidth??h["stroke-width"])*Number(t.size??t.width??h.width)/Number(e.size??e.width??h.width):e.strokeWidth??h["stroke-width"];return["svg",{...Object.entries(h).reduce((n,[i,d])=>(n[o(i)]=d,n),{}),..."color"in e&&e.color&&{[o("stroke")]:e.color},..."size"in e&&_(e.size)&&{[o("width")]:e.size,[o("height")]:e.size},..."width"in e&&_(e.width)&&{[o("width")]:e.width},..."height"in e&&_(e.height)&&{[o("height")]:e.height},[o("stroke-width")]:r,...f&&{[o("class")]:f},[o("viewBox")]:`0 0 ${c} ${l}`,...e.hasA11yProp===!1?{[o("aria-hidden")]:"true"}:{},..."attributes"in e&&e.attributes},t.node.map(n=>{const[i,d,w]=n,v=e.nonScalingStroke?{[o("vector-effect")]:"non-scaling-stroke",...d}:d;return w?[i,v,w]:[i,v]})]}/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const I=t=>{for(const e in t)if(e.startsWith("aria-")||e==="role"||e==="title")return!0;return!1};/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const z=t=>t==="";/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const T=t=>t==null?void 0:t.replace(/([a-z0-9])([A-Z])/g,"$1-$2").toLowerCase();/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const q=Symbol("lucide-icons");function V(){return L(q,{})}/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const E=({name:t,iconNode:e,"icon-node":a,icon:o={name:t&&T(t),node:e??a??[],size:24,aliases:[]},absoluteStrokeWidth:c,"absolute-stroke-width":l,nonScalingStroke:u,"non-scaling-stroke":k,strokeWidth:y,"stroke-width":f,size:r,width:x=r,height:g=r,color:m,...n},{slots:i})=>{var b;const{size:d,color:w,strokeWidth:v=2,absoluteStrokeWidth:$=!1,nonScalingStroke:D=!1,class:C=""}=V(),j=z(c)||z(l)||c===!0||l===!0||$===!0,A=z(u)||z(k)||u===!0||k===!0||D===!0;delete n.class;const p=(b=i.default)==null?void 0:b.call(i),[,N,W=[]]=P(o,{color:m??w,width:x??r??d,height:g??r??d,strokeWidth:y??f??v,absoluteStrokeWidth:j,nonScalingStroke:A,className:C,hasA11yProp:p!=null&&p.length>0||I(n),attributes:n});return M("svg",N,[...W.map(([B,H])=>M(B,H)),...p??[]])};/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */function s(t,e=[]){const a=typeof t=="string"?{name:t,node:e}:t;return(o,{slots:c})=>M(E,{...o,icon:a},c.default?{default:c.default}:void 0)}/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const F={name:"book-open",size:24,node:[["path",{d:"M12 5v16",key:"1f6ucr"}],["path",{d:"M20.001 19A2 2 0 0022 17V5a2 2 0 00-1.999-2L16 3.002A5 5 0 0012 5a5 5 0 00-4-2H4a2 2 0 00-2 2v12a2 2 0 001.999 2H8a5 5 0 014 2 5 5 0 014-2z",key:"1fyvmf"}]]},ie=s(F);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const U={name:"check",size:24,node:[["path",{d:"M20 6 9 17l-5-5",key:"1gmf2c"}]]},ae=s(U);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const O={name:"chevron-right",size:24,node:[["path",{d:"m9 18 6-6-6-6",key:"mthhwq"}]]},ce=s(O);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const R={name:"circle",size:24,node:[["circle",{cx:"12",cy:"12",r:"10",key:"1mglay"}]]},de=s(R);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const X={name:"file-text",size:24,node:[["path",{d:"M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z",key:"1oefj6"}],["path",{d:"M14 2v5a1 1 0 0 0 1 1h5",key:"wfsgrz"}],["path",{d:"M10 9H8",key:"b1mrlr"}],["path",{d:"M16 13H8",key:"t4e002"}],["path",{d:"M16 17H8",key:"z1uh3a"}]]},he=s(X);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const Z={name:"message-circle",size:24,node:[["path",{d:"M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719",key:"1sd12s"}]]},re=s(Z);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const G={name:"moon",size:24,node:[["path",{d:"M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401",key:"kfwtm"}]]},le=s(G);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const J={name:"panel-left",size:24,node:[["rect",{width:"18",height:"18",x:"3",y:"3",rx:"2",key:"afitv7"}],["path",{d:"M9 3v18",key:"fh3hqa"}]],aliases:["sidebar"]},ue=s(J);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const Q={name:"plus",size:24,node:[["path",{d:"M5 12h14",key:"1ays0h"}],["path",{d:"M12 5v14",key:"s699le"}]]},ke=s(Q);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const Y={name:"search",size:24,node:[["path",{d:"m21 21-4.34-4.34",key:"14j7rj"}],["circle",{cx:"11",cy:"11",r:"8",key:"4ej97u"}]]},ye=s(Y);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const K={name:"send",size:24,node:[["path",{d:"M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z",key:"1ffxy3"}],["path",{d:"m21.854 2.147-10.94 10.939",key:"12cjpa"}]]},fe=s(K);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ee={name:"settings",size:24,node:[["path",{d:"M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915",key:"1i5ecw"}],["circle",{cx:"12",cy:"12",r:"3",key:"1v7zrd"}]]},ge=s(ee);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const te={name:"sun",size:24,node:[["circle",{cx:"12",cy:"12",r:"4",key:"4exip2"}],["path",{d:"M12 2v2",key:"tus03m"}],["path",{d:"M12 20v2",key:"1lh1kg"}],["path",{d:"m4.93 4.93 1.41 1.41",key:"149t6j"}],["path",{d:"m17.66 17.66 1.41 1.41",key:"ptbguv"}],["path",{d:"M2 12h2",key:"1t8f8n"}],["path",{d:"M20 12h2",key:"1q8mjw"}],["path",{d:"m6.34 17.66-1.41 1.41",key:"1m8zz5"}],["path",{d:"m19.07 4.93-1.41 1.41",key:"1shlcs"}]]},me=s(te);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const oe={name:"trash",size:24,node:[["path",{d:"M10 11v6",key:"nco0om"}],["path",{d:"M14 11v6",key:"outv1u"}],["path",{d:"M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6",key:"miytrc"}],["path",{d:"M3 6h18",key:"d0wm0j"}],["path",{d:"M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",key:"e791ji"}]],aliases:["trash-2"]},we=s(oe);/**
 * @license @lucide/vue v1.53.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const ne={name:"users",size:24,node:[["path",{d:"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2",key:"1yyitq"}],["path",{d:"M16 3.128a4 4 0 0 1 0 7.744",key:"16gr8j"}],["path",{d:"M22 21v-2a4 4 0 0 0-3-3.87",key:"kshegd"}],["circle",{cx:"9",cy:"7",r:"4",key:"nufk8"}]]},ve=s(ne);export{ie as B,ce as C,he as F,re as M,ke as P,fe as S,we as T,ve as U,de as a,ae as b,le as c,me as d,ue as e,ge as f,ye as g};
