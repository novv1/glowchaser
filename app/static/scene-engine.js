/* 逐光者 GlowChaser · 场景引擎 M2（核心：噪声/8云属/染色/天板/太阳/星/主循环）
   移植基准：preview-redesign/style-h4-scene-live.html（附录 A 算法逐字移植，不换噪声）。
   M3 接管：雨/闪电/水面同步/灯火/山脊联动（DOM 层位已在 index 预留）。
   对外：window.SceneEngine {mount,set,play,stop,resize,params,genField,skyAt,shadeCloud,litRamp}。
   性能红线：云场离屏 340×120 / 远层 300×90；regen 仅参数变化 debounce 140ms；禁每帧 fBm；dt 封顶 50ms。 */

/* ═════════ 颜色工具 ═════════ */
function hx(c){return [parseInt(c.slice(1,3),16),parseInt(c.slice(3,5),16),parseInt(c.slice(5,7),16)];}
function lerp(a,b,t){return a+(b-a)*t;}
function mix(c1,c2,t){return [lerp(c1[0],c2[0],t)|0,lerp(c1[1],c2[1],t)|0,lerp(c1[2],c2[2],t)|0];}
function rgba(c,a){return 'rgba('+(c[0]|0)+','+(c[1]|0)+','+(c[2]|0)+','+a+')';}

/* ═════════ M7 · 视口/离屏基建 ═════════
   isNarrow：移动端（≤640px）统一判定，星点/雨丝/灯火减半都走它（附录B#5）。
   makeCanvas：离屏画布工厂——浏览器用 document.createElement，node 单测/海报离屏可注入，
   于是 shadeCloud/renderStill 不再硬依赖 document（同一套绘制代码两种环境都能跑）。 */
function isNarrow(){
  try{return (typeof matchMedia!=='undefined')&&matchMedia('(max-width:640px)').matches;}catch(e){return false;}
}
var canvasFactory=null;
function makeCanvas(w,h){
  var cv=null;
  if(typeof canvasFactory==='function'){try{cv=canvasFactory(w,h);}catch(e){cv=null;}}
  if(!cv&&typeof document!=='undefined'){try{cv=document.createElement('canvas');}catch(e){cv=null;}}
  if(!cv)return null;
  try{cv.width=w;cv.height=h;}catch(e){}
  return cv;
}

/* ═════════ M3 · 降水参数表（§2.6：雨丝数/长度/速度/斜率/雨幕） ═════════ */
var RAIN_P={
  drizzle:{n:130,len:14,spd:7,slope:.12,veil:.12},
  heavy:{n:340,len:22,spd:12,slope:.16,veil:.30},
  storm:{n:220,len:20,spd:11,slope:.20,veil:.22}
};
function rainAlpha(r){return (RAIN_P[r]||{veil:0}).veil;}
/* storm 天板青灰化（§2.6/附录B#1：mix #1a2230 55%），storm 时 lit 强制 ≤20 */
function stormMix(px){return px.map(function(c){return mix(c,hx('#1a2230'),.55);});}
/* 水面双色（§2.7）：w0/w1 随 lit/alt 漂移，返回 [w0,w1] */
function waterCols(litK,alt){
  var t1=Math.min(1,(1-litK)*.7+Math.max(0,-alt-2)*.1);
  var t2=Math.min(1,(1-litK)*.6);
  return [mix(hx('#d98a4e'),hx('#3a3e48'),t1),mix(hx('#8a4a52'),hx('#2a2e3a'),t2)];
}

/* ═════════ 参数状态（默认 B 中烧·卷云） ═════════ */
var SceneP={genus:'ci',cover:55,lit:78,alt:-2,rain:'none',seed:7};

/* ═════════ 太阳高度角 → 天板关键帧 ═════════ */
var SKY_KEYS=[
  [ 4, ['#3a68a8','#5a8abc','#8ab0c8','#d8c8a0','#f0dcaa']],
  [ 0, ['#16273e','#37435f','#a8535a','#f5a254','#ffd07a']],
  [-2, ['#101c30','#2c3a5a','#8a4a58','#e07a44','#f5b45e']],
  [-4, ['#0c1626','#24304e','#6a3e58','#b04a48','#d88a50']],
  [-6, ['#0a1220','#1c2642','#4e3450','#7a4048','#a06a52']],
  [-8, ['#080e1a','#141c30','#2c2842','#4e3448','#6a4a4c']]
];
function skyAt(alt){
  alt=Math.max(-8,Math.min(4,alt));
  for(var i=0;i<SKY_KEYS.length-1;i++){
    var a0=SKY_KEYS[i][0],c0=SKY_KEYS[i][1],a1=SKY_KEYS[i+1][0],c1=SKY_KEYS[i+1][1];
    if(alt<=a0&&alt>=a1){var t=(a0-alt)/(a0-a1);
      return c0.map(function(c,k){return mix(hx(c),hx(c1[k]),t);});}}
  return SKY_KEYS[SKY_KEYS.length-1][1].map(hx);
}
/* 染色色板（云的高度分带：低=金橙、中=橙红、高=粉紫），lit=0 时退化环境灰 */
var LIT_LO=hx('#ffc25e'),LIT_MID=hx('#ff7a45'),LIT_HI=hx('#d9708f');
function litRamp(t){
  return t<.5?mix(LIT_LO,LIT_MID,t*2):mix(LIT_MID,LIT_HI,(t-.5)*2);}

/* ═════════ 噪声云场（fBm value noise，附录 A 逐字移植） ═════════ */
function lcg(seed){var s=seed>>>0;return function(){return ((s=(s*1664525+1013904223)>>>0)/4294967296);};}
function makeGrid(rand,gw,gh){var g=new Float32Array(gw*gh);for(var i=0;i<gw*gh;i++)g[i]=rand();return g;}
function noise2(g,gw,gh,x,y){
  var x0=Math.floor(x)%gw,y0=Math.floor(y)%gh,x1=(x0+1)%gw,y1=(y0+1)%gh;
  var tx=x-Math.floor(x),ty=y-Math.floor(y);
  tx=tx*tx*(3-2*tx);ty=ty*ty*(3-2*ty);
  var a=g[y0*gw+x0],b=g[y0*gw+x1],c=g[y1*gw+x0],d=g[y1*gw+x1];
  return a*(1-tx)*(1-ty)+b*tx*(1-ty)+c*(1-tx)*ty+d*tx*ty;
}
function fbm(g,gw,gh,x,y,oct){
  var v=0,amp=.5,fx=1,fy=1;
  for(var o=0;o<oct;o++){v+=amp*noise2(g,gw,gh,x*fx,y*fy);amp*=.5;fx*=2.13;fy*=2.13;}
  return v;
}
/* 云场生成：返回 {A:Float32Array,w,h}；cover 0–1 */
function genField(type,cover,seed,W,H){
  statGen++;
  var rand=lcg(seed*7919+13);
  var gw=72,gh=36,g=makeGrid(rand,gw,gh);
  var A=new Float32Array(W*H);
  var th=1-cover*.92;
  var x,y,v,a;
  if(type==='ac'){          /* 高积云：细胞格团块 × 噪声扰动 */
    var cw=26,ch=13;
    var cells=[];
    for(var cy=0;cy<Math.ceil(H/ch)+1;cy++)for(var cx=0;cx<Math.ceil(W/cw)+1;cx++){
      cells.push({x:cx*cw+(rand()-.5)*cw*.7,y:cy*ch+(rand()-.5)*ch*.5,
        r:cw*(.34+rand()*.22),v:.55+rand()*.45});}
    for(y=0;y<H;y++)for(x=0;x<W;x++){
      v=0;
      for(var ci=0;ci<cells.length;ci++){var c=cells[ci],dx=x-c.x,dy=(y-c.y)*1.9,d2=dx*dx+dy*dy;
        v=Math.max(v,c.v*Math.max(0,1-d2/(c.r*c.r)));}
      v*=(.55+.45*fbm(g,gw,gh,x/W*4,y/H*4,3));
      a=(v-th)/(1-th+1e-4);a=Math.max(0,Math.min(1,a*1.5));a=a*a*(3-2*a);
      A[y*W+x]=a;}
  }else if(type==='cu'||type==='cb'){ /* 积云/积雨云：高斯团 + 噪声缘 */
    var blobs=[];
    var nb=type==='cb'?4:3;
    for(var bi=0;bi<nb;bi++)blobs.push({x:W*(.3+rand()*.4),y:H*(.45+rand()*.3),
      rx:W*(.14+rand()*.12)*(type==='cb'?1.25:1),ry:H*(.3+rand()*.2)});
    if(type==='cb')blobs.push({x:W*.5,y:H*.12,rx:W*.52,ry:H*.16}); /* 砧顶 */
    for(y=0;y<H;y++)for(x=0;x<W;x++){
      v=0;
      for(var qi=0;qi<blobs.length;qi++){var b=blobs[qi],ex=(x-b.x)/b.rx,ey=(y-b.y)/b.ry;
        v=Math.max(v,Math.exp(-(ex*ex+ey*ey)*1.6));}
      v*=(.6+.55*fbm(g,gw,gh,x/W*5,y/H*5,4));
      if(type==='cb'&&y>H*.62)v*=Math.max(0,1-(y-H*.62)/(H*.3)); /* 底部收束 */
      a=(v-th)/(1-th+1e-4);a=Math.max(0,Math.min(1,a*1.6));a=a*a*(3-2*a);
      A[y*W+x]=a;}
  }else{                            /* 纤维/幕/团块：各向异性 fBm */
    var cfg={ci:{ax:.16,oct:4,ridge:1},cs:{ax:.3,oct:3,ridge:0},sc:{ax:.7,oct:4,ridge:0},ns:{ax:.4,oct:2,ridge:0}}[type]||{ax:.5,oct:4,ridge:0};
    for(y=0;y<H;y++)for(x=0;x<W;x++){
      var n=fbm(g,gw,gh,x/W*cfg.ax*6,y/H*2.2,cfg.oct);
      if(cfg.ridge){n=1-Math.abs(2*n-1);n=Math.pow(n,1.45);}
      if(type==='ns')n=n*.75+.25;     /* 雨层云：均匀 */
      if(type==='sc')n=n*.85+.15*Math.max(0,1-y/(H*.5)); /* 层积云下部更实 */
      a=(n-th)/(1-th+1e-4);a=Math.max(0,Math.min(1,a*1.75));a=a*a*(3-2*a);
      A[y*W+x]=a;}
  }
  return {A:A,w:W,h:H};
}
/* 云染色：按高度分带 + 厚度暗化 → 离屏 canvas（仅 regen 时执行） */
function shadeCloud(field,cfg){
  var A=field.A,w=field.w,h=field.h;
  var cv=makeCanvas(w,h);
  if(!cv||!cv.getContext)return null;
  var cx2=null;
  try{cx2=cv.getContext('2d');}catch(e){return null;}
  if(!cx2)return null;
  var img=cx2.createImageData(w,h),D=img.data;
  var litK=cfg.lit;
  for(var y=0;y<h;y++){
    var band=litRamp(y/h);               /* 高度染色带 */
    var envT=y/h*.5+.1;                   /* 环境色混入比例（高处更环境） */
    for(var x=0;x<w;x++){
      var a=A[y*w+x],i=(y*w+x)*4;
      if(a<=0.004)continue;
      var c=mix(cfg.env,band,litK*(1-envT*.4));
      /* 厚度暗化：厚云压向暗色 */
      c=mix(c,cfg.dark,a*.5*(1-litK*.3));
      D[i]=c[0];D[i+1]=c[1];D[i+2]=c[2];D[i+3]=Math.min(255,a*430);
    }}
  cx2.putImageData(img,0,0);
  return cv;
}

/* ═════════ 主渲染 ═════════ */
var sceneCv=null,sceneCtx=null,sceneFW=0,sceneFH=0;
var cloudLayerY=0,cloudLayerH=0;
var skyNow=null,layers=[],drift=0;
var stars=[];var starN=0;
var rafId=0,running=false,resizeBound=false,roBound=false,regenTimer=null;
/* M7 观测量：性能红线可验证（禁每帧 fBm = genField 计数不随帧增长；regen 只在参数变化） */
var statFrames=0,statRegen=0,statGen=0,statStill=0;

/* 有效染色系数（0–1）：雷暴压 ≤20（附录B#1）；悬月云染色 ×.3（M6 §4.20 规则落到引擎）。 */
function litK(){
  var k=(SceneP.rain==='storm')?Math.min(SceneP.lit,20)/100:SceneP.lit/100;
  if(SceneP.moon==='full')k*=.3;
  return k;
}

function sizeSky(){
  if(!sceneCv)return;
  var w=sceneCv.clientWidth||640,h=sceneCv.clientHeight||270;
  sceneFW=sceneCv.width=Math.round(w*1.5);sceneFH=sceneCv.height=Math.round(h*1.5);
  cloudLayerY=sceneFH*.1;cloudLayerH=sceneFH*.62;
}
/* 云层状态：主层 340×120 + 远层 300×90（视差） */
function regenClouds(){
  layers=[];
  if(!sceneCtx)return;
  if(SceneP.cover<=0||SceneP.genus==='clear')return;
  statRegen++;
  if(!skyNow)skyNow=skyAt(SceneP.alt);   /* 兜底：首帧前先生成天板色 */
  var W=340,H=120;
  var env=mix(skyNow[1],hx('#3a3a50'),.3),dark=mix(skyNow[0],hx('#14141f'),.4);
  var cfg={lit:litK(),env:env,dark:dark};
  var main=shadeCloud(genField(SceneP.genus,SceneP.cover/100,SceneP.seed,W,H),cfg);
  if(!main)return;                       /* 离屏不可用：调用方静态兜底 */
  layers.push({cv:main,sp:.010,y:0,op:1});
  if(SceneP.cover>28&&['ns','cb'].indexOf(SceneP.genus)<0){ /* 远层薄云增密度 */
    var far=shadeCloud(genField(SceneP.genus==='ac'?'ci':'cs',(SceneP.cover/100)*.5,SceneP.seed+99,300,90),
                       {lit:litK()*.7,env:env,dark:dark});
    if(far)layers.push({cv:far,sp:.020,y:-sceneFH*.05,op:.7});
  }
}
function regenSoon(){if(typeof clearTimeout!=='undefined')clearTimeout(regenTimer);regenTimer=setTimeout(regenClouds,140);}

/* 太阳（含呼吸 + 低空压扁；§2.5 降水时不画） */
function drawSun(t){
  if(!sceneCtx)return;
  if(SceneP.rain!=='none')return;
  var alt=SceneP.alt;
  var horizonY=sceneFH*.73;
  var sx=sceneFW*.5,sy=horizonY-(alt/10)*sceneFH*.3;
  if(alt<-7.2)return;
  /* 色随高度：白→金→橙→红 */
  var sc=mix(hx('#fff6dd'),hx('#ff9a3d'),Math.min(1,Math.max(0,-alt/5)));
  var glow=(1.2-alt*.06)*.9;
  var br=1+.07*Math.sin(((t||0)*2*Math.PI)/7);   /* 7s 呼吸 */
  var hg=sceneCtx.createRadialGradient(sx,sy,0,sx,sy,sceneFW*.16*glow*br);
  hg.addColorStop(0,rgba(sc,.5));hg.addColorStop(.4,rgba(sc,.14));hg.addColorStop(1,rgba(sc,0));
  sceneCtx.fillStyle=hg;sceneCtx.fillRect(0,0,sceneFW,sceneFH);
  if(sy>horizonY+sceneFH*.06)return;
  /* 盘：低空大气折射压扁 */
  var squash=1-Math.min(.32,Math.max(0,-alt*.05));
  var r=sceneFH*.052;
  sceneCtx.save();sceneCtx.translate(sx,sy);sceneCtx.scale(1,squash);
  var dg=sceneCtx.createRadialGradient(0,-r*.2,r*.1,0,0,r);
  dg.addColorStop(0,rgba(mix(sc,hx('#ffffff'),.5),1));dg.addColorStop(.6,rgba(sc,1));dg.addColorStop(1,rgba(mix(sc,hx('#c8501a'),.4),.95));
  sceneCtx.fillStyle=dg;
  sceneCtx.beginPath();sceneCtx.arc(0,0,r,0,Math.PI*2);sceneCtx.fill();
  sceneCtx.restore();
}

/* 月盘（M6：v1.0 §4.20 悬月满月盘；§2.5 降水时不画，与日盘同规）
   moon: 'full' 显示满月盘 #f2f0e8（月出档夜空帧），'none' 不画。
   位置与日盘对称（偏右上，避字幕带）；月晕 + 盘面明暗（染色×.3 挂 SceneP.moonlit 预留）。 */
function drawMoon(t){
  if(!sceneCtx)return;
  if(SceneP.moon!=='full')return;
  if(SceneP.rain!=='none')return;
  var horizonY=sceneFH*.73;
  var mx=sceneFW*.66,my=horizonY-sceneFH*.30;
  var r=sceneFH*.046;
  var mc=hx('#f2f0e8');
  /* 月晕 */
  var hg=sceneCtx.createRadialGradient(mx,my,0,mx,my,sceneFW*.11);
  hg.addColorStop(0,rgba(mc,.30));hg.addColorStop(.5,rgba(mc,.08));hg.addColorStop(1,rgba(mc,0));
  sceneCtx.fillStyle=hg;sceneCtx.fillRect(0,0,sceneFW,sceneFH);
  /* 盘：满月冷白 + 轻微环形山明暗 */
  var dg=sceneCtx.createRadialGradient(mx-r*.3,-0+my-r*.3,r*.1,mx,my,r);
  dg.addColorStop(0,rgba(mix(mc,hx('#ffffff'),.4),1));
  dg.addColorStop(.75,rgba(mc,.98));dg.addColorStop(1,rgba(mix(mc,hx('#9aa0b0'),.35),.95));
  sceneCtx.fillStyle=dg;
  sceneCtx.beginPath();sceneCtx.arc(mx,my,r,0,Math.PI*2);sceneCtx.fill();
  /* 盘面斑（3 块低 alpha 灰，固定位 —— 确定性，不随 seed） */
  sceneCtx.fillStyle=rgba(hx('#c9ccd4'),.35);
  [[-.3,-.1,.22],[.25,.15,.18],[.05,-.35,.14]].forEach(function(b){
    sceneCtx.beginPath();sceneCtx.arc(mx+b[0]*r,my+b[1]*r,b[2]*r,0,Math.PI*2);sceneCtx.fill();});
}

/* 天板 + 维纳斯带 */
function drawSky(){
  if(!sceneCtx)return;
  skyNow=skyAt(SceneP.alt);
  var pal=skyNow;
  if(SceneP.rain==='storm')pal=stormMix(pal);   /* 附录B#1：雷暴天板青灰化 */
  var g=sceneCtx.createLinearGradient(0,0,0,sceneFH);
  var pos=[0,.22,.45,.68,.88];
  pal.forEach(function(c,i){g.addColorStop(pos[i],rgba(c,1));});
  sceneCtx.fillStyle=g;sceneCtx.fillRect(0,0,sceneFW,sceneFH);
  /* 维纳斯带：太阳落山后，地平线上方的粉紫反照弧（光板暮光的标志） */
  if(SceneP.alt<0&&SceneP.cover<40){
    var vy=sceneFH*.62,vh=sceneFH*.16,b=Math.min(1,-SceneP.alt/4)*.8;
    var vg=sceneCtx.createLinearGradient(0,vy,0,vy+vh);
    vg.addColorStop(0,rgba(hx('#c88aa8'),.34*b));
    vg.addColorStop(.55,rgba(hx('#8a6a9a'),.2*b));
    vg.addColorStop(1,'rgba(0,0,0,0)');
    sceneCtx.fillStyle=vg;sceneCtx.fillRect(0,vy,sceneFW,vh);
  }
}

/* 云层绘制（漂移 + 视差 + 镜像拼接） */
function drawClouds(dt){
  if(!sceneCtx||!layers.length)return;
  drift+=dt/1000;   /* dt 毫秒 → 秒；sp 单位 = 屏宽比例/秒 */
  layers.forEach(function(L){
    var dw=sceneFW*1.15,dh=cloudLayerH*(L.y?0.75:1);
    var dx=-((drift*L.sp*sceneFW)%dw);
    sceneCtx.globalAlpha=L.op;
    sceneCtx.drawImage(L.cv,dx,cloudLayerY+L.y,dw,dh);
    sceneCtx.drawImage(L.cv,dx+dw,cloudLayerY+L.y,dw,dh);
    sceneCtx.globalAlpha=1;});
}

/* 星星 */
function starAmount(){
  var dark=Math.max(0,(-SceneP.alt-1)/5);           /* 天越黑星越多 */
  var clear=1-SceneP.cover/130;                      /* 云越少星越多 */
  return Math.min(1,dark*clear);
}
function drawStars(t){
  if(!sceneCtx)return;
  var amt=starAmount();if(amt<=.02)return;
  stars.forEach(function(s){var a=(.3+.7*Math.abs(Math.sin(t*s.s+s.p)))*amt;
    sceneCtx.fillStyle=rgba([255,250,240],a*.85);
    sceneCtx.beginPath();sceneCtx.arc(s.x*sceneFW,s.y*sceneFH,s.r,0,Math.PI*2);sceneCtx.fill();});
}

/* 主循环：dt 封顶 50ms 防后台标签页跳变 */
var lastT=0;
function sceneStep(now){
  if(!running)return;
  statFrames++;
  var dt=Math.min(50,now-lastT);lastT=now;var t=now/1000;
  drawSky();
  drawStars(t);
  drawSun(t);
  drawMoon(t);
  drawClouds(dt);
  drawRain(dt);
  if(typeof requestAnimationFrame!=='undefined')rafId=requestAnimationFrame(sceneStep);
  else running=false;
}
function drawOnceStatic(){
  drawSky();drawStars(0);drawSun(0);drawMoon(0);drawClouds(16);drawRain(16);
  groundSync();
}
function scenePlay(){
  sceneStop();
  var rm=false;
  try{rm=(typeof matchMedia!=='undefined')&&matchMedia('(prefers-reduced-motion: reduce)').matches;}catch(e){}
  if(rm||typeof requestAnimationFrame==='undefined'){drawOnceStatic();return;}  /* 降级：静态帧 */
  running=true;
  lastT=(typeof performance!=='undefined')?performance.now():0;
  rafId=requestAnimationFrame(sceneStep);
}
function sceneStop(){
  running=false;
  boltHalt();
  if(rafId&&typeof cancelAnimationFrame!=='undefined')cancelAnimationFrame(rafId);
  rafId=0;
}

/* ═════════ M3 · 雨/闪电/地景（§2.6/§2.7：独立雨层 + 枝状闪电单例 + 水面灯火） ═════════ */
var rainCv=null,rainCtx=null,rainDrops=[],rainW=0,rainH=0;
var boltRunning=false,boltSvg=null,screenFlash=null,boltTimer=null;
var waterEl=null,roadEl=null,cityBox=null,lampsDim=false;

function seedRain(){
  rainDrops=[];
  var P=RAIN_P[SceneP.rain];if(!P)return;
  var n=P.n;
  if(isNarrow())n=Math.round(n*.6);   /* 附录B#5：移动端雨丝数 ×.6 */
  for(var i=0;i<n;i++)rainDrops.push({x:Math.random(),y:Math.random(),v:.7+Math.random()*.6});
}
function sizeRain(){
  if(!rainCv||typeof document==='undefined')return;
  var r=rainCv.getBoundingClientRect();
  rainW=rainCv.width=Math.max(60,Math.round((r.width||640)*1.25));
  rainH=rainCv.height=Math.max(60,Math.round((r.height||270)*1.25));
}
function drawRain(dt){
  if(!rainCtx)return;
  var P=RAIN_P[SceneP.rain];
  if(!P){rainCtx.clearRect(0,0,rainW||9999,rainH||9999);return;}
  var w=rainW,h=rainH;
  rainCtx.clearRect(0,0,w,h);
  rainCtx.strokeStyle='rgba(190,205,230,.5)';rainCtx.lineWidth=Math.max(1,w/900);
  rainCtx.beginPath();
  var step=P.spd*dt/1000;
  for(var i=0;i<rainDrops.length;i++){
    var d=rainDrops[i];
    d.y+=step*d.v;if(d.y>1.02){d.y=-.02;d.x=Math.random();}
    var x0=d.x*w,y0=d.y*h,len=P.len*(h/270);
    rainCtx.moveTo(x0,y0);rainCtx.lineTo(x0-P.slope*len,y0+len);
  }
  rainCtx.stroke();
  /* 雨幕：底部 38% 线性渐雾 */
  var vg=rainCtx.createLinearGradient(0,h*.62,0,h);
  vg.addColorStop(0,'rgba(150,160,190,0)');
  vg.addColorStop(1,'rgba(150,160,190,'+P.veil+')');
  rainCtx.fillStyle=vg;rainCtx.fillRect(0,h*.62,w,h*.38);
}
/* 枝状闪电：主闪 110ms → 170ms 后回闪 80ms；屏闪 .26/300ms；2.2–6.4s 随机递归；单例 */
function boltOnce(){
  boltRunning=true;
  if(!boltSvg){boltRunning=false;return;}
  var W=1200,H=500,x=200+Math.random()*800,y=40,segs=6+(Math.random()*5|0),pts=[[x,y]];
  for(var i=0;i<segs;i++){x+=(Math.random()-.5)*110;y+=42+Math.random()*36;pts.push([x,y]);}
  var pl='M'+pts.map(function(p){return p[0].toFixed(0)+','+p[1].toFixed(0);}).join(' L');
  var ns='http://www.w3.org/2000/svg';
  boltSvg.innerHTML='';
  try{
    var pEl=document.createElementNS(ns,'polyline');
    pEl.setAttribute('points',pl.slice(1).replace(/ L/g,' '));
    pEl.setAttribute('fill','none');pEl.setAttribute('stroke','#f4f6ff');
    pEl.setAttribute('stroke-width','3.4');
    pEl.setAttribute('style','filter:drop-shadow(0 0 9px #cfe0ff) drop-shadow(0 0 26px #9db8ff)');
    boltSvg.appendChild(pEl);
    var cloud=document.createElementNS(ns,'ellipse');
    cloud.setAttribute('cx',pts[0][0]);cloud.setAttribute('cy',pts[0][1]-16);
    cloud.setAttribute('rx','150');cloud.setAttribute('ry','30');
    cloud.setAttribute('fill','rgba(230,238,255,.22)');
    boltSvg.insertBefore(cloud,boltSvg.firstChild);   /* 云内闪 */
  }catch(e){}
  if(screenFlash){screenFlash.style.transition='none';screenFlash.style.opacity='.26';}
  setTimeout(function(){
    if(boltSvg.firstChild)boltSvg.firstChild.setAttribute('opacity','.15');  /* 主闪 110ms */
    setTimeout(function(){                                  /* 170ms 后回闪 80ms */
      if(boltSvg.firstChild)boltSvg.firstChild.setAttribute('opacity','1');
      if(screenFlash){screenFlash.style.transition='opacity .3s';screenFlash.style.opacity='0';}
      setTimeout(function(){
        if(boltSvg)boltSvg.innerHTML='';
        boltTimer=setTimeout(boltOnce,2200+Math.random()*4200);
      },80);
    },170);
  },110);
}
function boltKick(){
  if(boltRunning)return;                                  /* 单例：防重复挂链 */
  if(SceneP.rain!=='storm')return;
  var rm=false;
  try{rm=(typeof matchMedia!=='undefined')&&matchMedia('(prefers-reduced-motion: reduce)').matches;}catch(e){}
  if(rm)return;                                           /* 降级：雨/闪停 */
  boltOnce();
}
function boltHalt(){
  boltRunning=false;
  if(boltTimer&&typeof clearTimeout!=='undefined')clearTimeout(boltTimer);
  boltTimer=null;
  if(boltSvg)boltSvg.innerHTML='';
  if(screenFlash){screenFlash.style.transition='none';screenFlash.style.opacity='0';}
}
/* 地景联动（§2.7）：水面双色 + 光路 + 灯火（桌面 11 点 / 移动端 6 点，附录B#5；雨/雷 ×.6） */
function lampCount(){return isNarrow()?6:11;}
function cityTwinkle(i){
  return 'cn-cityTw '+(1.6+((i*7919)%3000)/1000).toFixed(1)+'s ease-in-out '+(0-(i*.37)%1.6).toFixed(1)+'s infinite';
}
function groundSync(){
  var litKl=litK();
  var cols=waterCols(litKl,SceneP.alt);
  if(waterEl){
    waterEl.style.background='linear-gradient(180deg,'+rgba(cols[0],1)+' 0%,'+rgba(mix(cols[0],cols[1],.5),1)+' 36%,'+rgba(cols[1],1)+' 100%)';
  }
  if(roadEl){
    if(SceneP.alt>-6&&SceneP.rain==='none'){
      roadEl.style.opacity=(.2+SceneP.lit*.006).toFixed(2);roadEl.style.display='';
    }else roadEl.style.display='none';
  }
  lampsDim=(SceneP.rain!=='none');
  if(cityBox){
    var dim=lampsDim?.6:1;
    for(var i=0;i<cityBox.childNodes.length;i++){
      var n=cityBox.childNodes[i];
      if(n.style)n.style.opacity=(+n.getAttribute('data-b')*dim).toFixed(2);
    }
  }
}
function groundMount(){
  if(typeof document==='undefined')return;
  rainCv=document.getElementById('cvRain');
  try{rainCtx=rainCv?rainCv.getContext('2d'):null;}catch(e){rainCtx=null;}
  boltSvg=document.getElementById('boltSvg');
  screenFlash=document.getElementById('screenflash');
  waterEl=document.getElementById('Lwater');roadEl=document.getElementById('waterRoad');
  cityBox=document.getElementById('citylights');
  if(cityBox&&!cityBox.childNodes.length){
    var ln=lampCount();
    for(var i=0;i<ln;i++){   /* 灯火：金/白随机，1.6–4.6s 闪烁，落在山脊带上（移动端 6 点） */
      var s=document.createElement('i');
      var gold=i%2===0;
      s.style.left=(6+i*(ln===11?8.2:15)+((i*37)%23)/10)+'%';s.style.top=(30+((i*53)%55))+'%';
      s.style.background=gold?'#ffd9a0':'#eef2ff';
      s.style.animation=cityTwinkle(i);
      s.setAttribute('data-b',(.55+((i*29)%45)/100).toFixed(2));
      s.style.opacity=s.getAttribute('data-b');
      cityBox.appendChild(s);
    }
  }
  if(rainCv){sizeRain();seedRain();}
}

/* ═════════ M4 · 预报数据 → 场景参数映射（§3 纯函数，单元可测） ═════════
   真实报告字段（app/services/report.py 实测）：
   main.cloud{total,low,mid,high}（:938-941）/ main.weather.precip_prob（:947）/
   astronomy{key_time}（无 key_alt，:1635-1660）/ tracks{sun_alt,time}（:356-368）/
   illumination.sun_alt_deg（:595）/ probability / meta{date,type}（无 cityId，:1627-1630）/
   risks[]+reason（雷暴文案兜底）。
   alt 优先级：illumination.sun_alt_deg → tracks.sun_alt[key_time 索引] → -2 默认。
   seed 优先级：input.cityId → report.cityId → meta.date+genus（meta 无 cityId）。
   诚实降级：报告无 precipitation_mm/CAPE/雷暴专字段——降水量档用
   precip_prob 代理（≥70→heavy、>20→drizzle），后端补字段后升级。 */
function hashStr(s){var h=0;for(var i=0;i<s.length;i++)h=((h*31)+s.charCodeAt(i))|0;return Math.abs(h);}
function mapReportToScene(r,input){
  r=r||{};input=input||{};
  var main=r.main||{},cloud=main.cloud||{},wx=main.weather||{};
  var low=+cloud.low||0,mid=+cloud.mid||0,high=+cloud.high||0;
  var total=(cloud.total!=null)?+cloud.total:Math.min(100,low+mid+high);
  var prob=(r.probability!=null)?+r.probability:50;
  var astro=r.astronomy||{};
  var alt=null;
  if(r.illumination&&r.illumination.sun_alt_deg!=null)alt=+r.illumination.sun_alt_deg;
  else if(r.tracks&&r.tracks.sun_alt&&r.tracks.time&&astro.key_time){
    var ti=r.tracks.time.indexOf(astro.key_time);
    if(ti>=0&&r.tracks.sun_alt[ti]!=null)alt=+r.tracks.sun_alt[ti];}
  if(alt==null||!isFinite(alt))alt=-2;
  alt=Math.max(-8,Math.min(4,alt));
  var pprob=(wx.precip_prob!=null)?+wx.precip_prob:0;
  /* 雷暴：risks/reason 文案含雷暴/对流/闪电（现有字段兜底；后端补专字段后升级） */
  var txt=((r.risks||[]).join(' ')+' '+(r.reason||''));
  var storm=/雷暴|对流|闪电|thunder|storm/i.test(txt);
  /* genus 优先级（§3）：Ns→Cb→Sc→Ac→Cs→Ci→Cu→clear（As 归 Cs） */
  var genus='ci';
  if(low>85&&pprob>0)genus='ns';
  else if(storm||((mid+high)>55&&(r.cape>800)))genus='cb';
  else if(low>=60)genus='sc';
  else if(mid>=50&&high<30)genus='ac';
  else if(mid>=40)genus='cs';                    /* As 归 Cs：中云厚且高云少 */
  else if(high>=50)genus='cs';
  else if(high>=20)genus='ci';
  else if(low>=15)genus='cu';
  else if(total<12)genus='clear';
  else genus='ci';
  /* rain：雷暴→storm；量档无 mm 字段，用 pprob 代理（≥70 heavy、>20 drizzle） */
  var rain='none';
  if(storm)rain='storm';
  else if(pprob>=70)rain='heavy';
  else if(pprob>20)rain='drizzle';
  var lit=Math.max(0,Math.min(100,Math.round(prob)));
  if(rain!=='none'&&lit>20)lit=20;
  var cover=Math.max(0,Math.min(100,Math.round(total)));
  var meta=r.meta||{};
  var cityId=input.cityId||r.cityId||meta.cityId||meta.city||'';
  var seed=hashStr(String(cityId)+'|'+String(meta.date||'')+'|'+genus)%997;
  return {genus:genus,cover:cover,lit:lit,alt:alt,rain:rain,seed:seed};
}
function sceneMount(els){
  els=els||{};
  var cv=els.sky||els.cvSky||'cvSky';
  if(typeof cv==='string'&&typeof document!=='undefined')cv=document.getElementById(cv);
  if(!cv||!cv.getContext)return false;
  try{sceneCtx=cv.getContext('2d');}catch(e){return false;}
  if(!sceneCtx)return false;   /* 2D 不可用：调用方保持静态渐变兜底，不抛错 */
  sceneCv=cv;
  var n=isNarrow()?45:90;   /* 附录B#5：移动端星点减半 */
  stars=[];for(var i=0;i<n;i++)stars.push({x:Math.random(),y:Math.random()*.5,r:Math.random()*1.2+.4,p:Math.random()*6.28,s:.5+Math.random()*1.4});
  starN=stars.length;   /* 只读探针 starCount()：三档截图脚本验收「移动端星点减半」 */
  sizeSky();
  skyNow=skyAt(SceneP.alt);
  groundMount();
  groundSync();
  regenClouds();
  seedRain();
  boltHalt();boltKick();
  if(!resizeBound&&typeof addEventListener!=='undefined'){
    addEventListener('resize',function(){sizeSky();sizeRain();groundSync();regenSoon();});resizeBound=true;}
  /* 首屏帧在挂载时是 display:none（clientWidth=0），只监听 window.resize 会让
     画布永远停在 640×270 兜底分辨率、被 CSS 拉伸糊掉。用 ResizeObserver 捕捉
     「报告到达 → 帧显示」这一刻的尺寸变化，重新测量并重建云场。 */
  if(!roBound&&typeof ResizeObserver!=='undefined'){
    try{
      new ResizeObserver(function(){
        if(!sceneCv)return;
        if(Math.abs(sceneCv.clientWidth*1.5-sceneCv.width)>2||
           Math.abs(sceneCv.clientHeight*1.5-sceneCv.height)>2){
          sceneResize();
        }
      }).observe(cv);
      roBound=true;
    }catch(e){}
  }
  scenePlay();
  return true;
}
function sceneSet(p){
  p=p||{};
  if(p.genus!==undefined)SceneP.genus=p.genus;
  if(p.cover!==undefined)SceneP.cover=Math.max(0,Math.min(100,+p.cover||0));
  if(p.lit!==undefined)SceneP.lit=Math.max(0,Math.min(100,+p.lit||0));
  if(p.alt!==undefined)SceneP.alt=Math.max(-8,Math.min(4,+p.alt||0));
  if(p.rain!==undefined)SceneP.rain=p.rain;
  if(SceneP.rain==='storm'&&SceneP.lit>20)SceneP.lit=20;  /* 附录B#1：雷暴 lit≤20 */
  if(p.seed!==undefined){var s=+p.seed;SceneP.seed=isFinite(s)?(s|0):7;}
  if(p.moon!==undefined)SceneP.moon=(p.moon==='full'?'full':'none');  /* M6：悬月满月盘 */
  seedRain();groundSync();boltHalt();boltKick();
  regenSoon();
}
function sceneResize(){sizeSky();sizeRain();groundSync();regenSoon();}

/* ═════════ M7 · 离屏静帧（海报顶部帧 = 页面同场景；v2.0 §4 海报行） ═════════
   renderStill({w,h,params,canvas,t}) —— 复用同一套 draw*（天板/星/日/月/云/地景/雨），
   做法是把模块级 target 临时换到离屏 canvas，画完原样换回：
     · 不启动 rAF、不进主循环（海报生成不抢占页面帧）
     · 不写回 SceneP（params 只在调用期间浅覆盖，finally 恢复）
     · 无 document 且无注入工厂 → null，调用方退回原生渐变
   地景（水面/山脊/灯火）在页面里是 DOM 层，这里按同一参数画等效地景带，保证"海报=页面所见"。 */
function stillGround(cx,w,h){
  var horizonY=h*.73;
  var lk=litK();
  var cols=waterCols(lk,SceneP.alt);
  var g=cx.createLinearGradient(0,horizonY,0,h);
  g.addColorStop(0,rgba(mix(cols[0],cols[1],.5),1));
  g.addColorStop(.36,rgba(cols[1],1));
  g.addColorStop(1,rgba(mix(cols[1],hx('#0a0c14'),.55),1));
  cx.fillStyle=g;cx.fillRect(0,horizonY,w,h-horizonY);
  /* 水面光路（§2.7：alt>-6 且无降水时才有） */
  if(SceneP.alt>-6&&SceneP.rain==='none'){
    var rg=cx.createLinearGradient(0,horizonY,0,h);
    rg.addColorStop(0,rgba(hx('#ffd9a0'),Math.max(.05,Math.min(.45,.05+SceneP.lit*.004))));
    rg.addColorStop(1,rgba(hx('#ffd9a0'),0));
    cx.fillStyle=rg;
    cx.beginPath();cx.moveTo(w*.43,horizonY);cx.lineTo(w*.57,horizonY);
    cx.lineTo(w*.74,h);cx.lineTo(w*.26,h);cx.closePath();cx.fill();
  }
  /* 山脊：同 seed 确定性剪影（远/近两叠，页面里是 #Lridges 的 SVG 层） */
  var rnd=lcg(SceneP.seed*7919+101);
  [[hx('#171b26'),.30,1],[hx('#0c1018'),.19,.7]].forEach(function(rg2,k){
    var base=horizonY+(k?h*.03:0),amp=h*rg2[1]*.34,ph=rnd()*6.28;
    cx.fillStyle=rgba(rg2[0],1);
    cx.beginPath();cx.moveTo(0,h);
    for(var x=0;x<=w;x+=Math.max(4,w/48)){
      var y=base-amp*(.55+.45*Math.sin(x/w*6.2+ph)*rg2[2])-amp*.22*Math.sin(x/w*17+ph*2);
      cx.lineTo(x,y);
    }
    cx.lineTo(w,h);cx.closePath();cx.fill();
  });
  /* 灯火：与页面同规则（金/白交替，亮度 data-b；降水时 ×.6 压暗） */
  var n=lampCount(),dim=(SceneP.rain!=='none')?.6:1;
  for(var i=0;i<n;i++){
    var lx=w*(.06+i*(n===11?8.2:15)/100)+((i*37)%23)/1000*w*.01;
    var ly=horizonY-h*(.012+((i*53)%55)/100*.045);
    cx.fillStyle=rgba(i%2===0?hx('#ffd9a0'):hx('#eef2ff'),dim*(.55+((i*29)%45)/100));
    cx.beginPath();cx.arc(lx,ly,Math.max(1,h*.007),0,Math.PI*2);cx.fill();
  }
}
function renderStill(opts){
  opts=opts||{};
  if(typeof canvasFactory!=='function'&&typeof document==='undefined')return null;
  var w=Math.max(2,Math.round(+opts.w||1080)),h=Math.max(2,Math.round(+opts.h||600));
  var cv=opts.canvas||makeCanvas(w,h);
  if(!cv||!cv.getContext)return null;
  var cx=null;
  try{cx=cv.getContext('2d');}catch(e){cx=null;}
  if(!cx)return null;
  var keepP={genus:SceneP.genus,cover:SceneP.cover,lit:SceneP.lit,alt:SceneP.alt,
             rain:SceneP.rain,seed:SceneP.seed,moon:SceneP.moon};
  var keep={cv:sceneCv,ctx:sceneCtx,fw:sceneFW,fh:sceneFH,cy:cloudLayerY,ch:cloudLayerH,
            sky:skyNow,lay:layers,dr:drift};
  var p=opts.params||{};
  if(p.genus!==undefined)SceneP.genus=p.genus;
  if(p.cover!==undefined)SceneP.cover=Math.max(0,Math.min(100,+p.cover||0));
  if(p.lit!==undefined)SceneP.lit=Math.max(0,Math.min(100,+p.lit||0));
  if(p.alt!==undefined)SceneP.alt=Math.max(-8,Math.min(4,+p.alt||0));
  if(p.rain!==undefined)SceneP.rain=p.rain;
  if(p.seed!==undefined){var s=+p.seed;SceneP.seed=isFinite(s)?(s|0):7;}
  if(p.moon!==undefined)SceneP.moon=(p.moon==='full'?'full':'none');
  var t=+opts.t||0;
  var ok=true;
  try{
    sceneCv=cv;sceneCtx=cx;sceneFW=w;sceneFH=h;
    cloudLayerY=h*.1;cloudLayerH=h*.62;
    layers=[];skyNow=skyAt(SceneP.alt);regenClouds();
    drawSky();drawStars(t);drawSun(t);drawMoon(t);drawClouds(16);
    if(opts.ground!==false)stillGround(cx,w,h);
    if(SceneP.rain!=='none'){
      var kd=rainDrops,kc=rainCtx,kw=rainW,kh=rainH;
      seedRain();rainCtx=cx;rainW=w;rainH=h;drawRain(16);
      rainDrops=kd;rainCtx=kc;rainW=kw;rainH=kh;
    }
    statStill++;
  }catch(e){ ok=false; /* 离屏失败：调用方退回原生渐变 */}
  finally{
    SceneP.genus=keepP.genus;SceneP.cover=keepP.cover;SceneP.lit=keepP.lit;
    SceneP.alt=keepP.alt;SceneP.rain=keepP.rain;SceneP.seed=keepP.seed;SceneP.moon=keepP.moon;
    sceneCv=keep.cv;sceneCtx=keep.ctx;sceneFW=keep.fw;sceneFH=keep.fh;
    cloudLayerY=keep.cy;cloudLayerH=keep.ch;skyNow=keep.sky;layers=keep.lay;drift=keep.dr;
  }
  return ok?cv:null;
}

/* ═════════ M7 · 悬日月窗口 → 场景参数（§3 同一映射的窗口版，纯函数） ═════════
   窗口没有 main.cloud，只有视线交点云量 cloud_at —— 取最厚层（与"挡不挡得住"同一口径）。
   alt 取窗口天空的太阳高度角 sky.sun_alt（有则用）→ apparent_alt → best_alt。
   悬月：moon='full'（满月盘），云染色 ×.3 由 litK() 统一乘，映射本身不缩放 lit。 */
function mapAlignToScene(w,base){
  w=w||{};base=base||{};
  var c=w.cloud_at||{},sky=w.sky||{},f=w.feasibility||{},dims=f.dims||{};
  var low=+c.low||0,mid=+c.mid||0,high=+c.high||0;
  var thick=Math.max(low,mid,high);
  var alt=(sky.sun_alt!=null)?+sky.sun_alt
    :(w.apparent_alt!=null?+w.apparent_alt:(w.best_alt!=null?+w.best_alt:-2));
  var per=String(w.period||'')+' '+String(w.type||'');
  var pseudo={
    main:{cloud:{low:low,mid:mid,high:high,total:thick},
          weather:{precip_prob:(dims.precip!=null)?Math.max(0,100-(+dims.precip)):0}},
    probability:(w.vis_prob!=null)?+w.vis_prob:((f.score!=null)?+f.score:50),
    illumination:{sun_alt_deg:alt},
    risks:f.risks||[],reason:w.reason||'',
    astronomy:{key_time:w.best_time},
    meta:{date:w.date||'',type:/sunrise|日出/.test(per)?'sunrise':'sunset'}
  };
  var p=mapReportToScene(pseudo,base);
  p.moon=/月/.test(String(w.type||''))||w.body==='moon'?'full':'none';
  return p;
}

var SceneEngine={
  mount:sceneMount,set:sceneSet,play:scenePlay,stop:sceneStop,resize:sceneResize,
  params:function(){return {genus:SceneP.genus,cover:SceneP.cover,lit:SceneP.lit,alt:SceneP.alt,rain:SceneP.rain,seed:SceneP.seed,moon:SceneP.moon||'none'};},
  rainDrops:function(){return rainDrops.length;},boltArmed:function(){return boltRunning;},
  starCount:function(){return starN;},
  lampsDimmed:function(){return lampsDim;},moonDisc:drawMoon,
  genField:genField,skyAt:skyAt,shadeCloud:shadeCloud,litRamp:litRamp,
  RAIN_P:RAIN_P,rainAlpha:rainAlpha,stormMix:stormMix,waterCols:waterCols,
  mapReportToScene:mapReportToScene,
  renderStill:renderStill,mapAlignToScene:mapAlignToScene,litK:litK,lampCount:lampCount,
  stats:function(){return {frames:statFrames,regen:statRegen,genField:statGen,still:statStill};},
  resetStats:function(){statFrames=0;statRegen=0;statGen=0;statStill=0;},
  __setCanvasFactory:function(f){canvasFactory=(typeof f==='function')?f:null;}
};
if(typeof window!=='undefined'){window.SceneEngine=SceneEngine;}
